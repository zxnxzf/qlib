"""Conservative Guojin standard-QMT bridge, read-only until probe binding.

Account, position, and full-tick calls reuse the already proven iQuant-family
read APIs.  Submission/recovery deliberately stay locked until the user's
``qmt_probe.json`` confirms Guojin's order, deal, status, fee, and cancel API.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

from .qmt_strategy import QmtExecutionError, normalize_code, qmt_code


SHANGHAI_TZ = timezone(timedelta(hours=8))
DEFAULT_MAX_QUOTE_AGE_SECONDS = 30.0
MAX_FUTURE_QUOTE_SKEW_SECONDS = 5.0
_ISO_TIMESTAMP_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$"
)
_COMPACT_TIMESTAMP_RE = re.compile(r"^\d{14}(?:\d{3})?$")
_EPOCH_TIMESTAMP_RE = re.compile(r"^\d{10}(?:\d{3})?$")


def _objects(value, record_fields=()):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, dict):
        if any(field in value for field in record_fields):
            return [value]
        return list(value.values())
    return [value]


def _position_records(value):
    if value is None:
        return []
    if isinstance(value, dict):
        if any(field in value for field in POSITION_RECORD_FIELDS):
            return [(value, "")]
        return [(position, str(code)) for code, position in value.items()]
    if isinstance(value, (list, tuple)):
        return [(position, "") for position in value]
    return [(value, "")]


def _field(value, names, default=None):
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _finite_or_none(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _number(value, default=0.0):
    number = _finite_or_none(value)
    if number is None:
        return default
    return number


def _now_shanghai():
    return datetime.now(SHANGHAI_TZ)


ACCOUNT_CASH_FIELDS = ("m_dAvailable", "available", "cash", "enable_balance")
ACCOUNT_MARKET_VALUE_FIELDS = (
    "m_dStockValue",
    "m_dInstrumentValue",
    "m_dMarketValue",
    "market_value",
)
ACCOUNT_TOTAL_ASSET_FIELDS = ("m_dTotalAsset", "total_asset")
POSITION_CODE_FIELDS = ("m_strInstrumentID", "stock_code", "code", "instrument")
POSITION_SHARES_FIELDS = ("m_nVolume", "volume", "shares", "position")
POSITION_AVAILABLE_FIELDS = ("m_nCanUseVolume", "can_use_volume", "available", "available_shares")
POSITION_RECORD_FIELDS = POSITION_CODE_FIELDS + POSITION_SHARES_FIELDS + POSITION_AVAILABLE_FIELDS
_NORMALIZED_CODE_RE = re.compile(r"^(?:SH|SZ|BJ)\d{6}$")


class GuojinQmtBridge:
    """Read-only adapter usable for the first Windows preflight."""

    def __init__(self, context_info, config, api_namespace=None, now_fn=None):
        self.context_info = context_info
        self.config = dict(config)
        self.api = api_namespace or {}
        self.now_fn = now_fn or _now_shanghai
        max_age = _finite_or_none(self.config.get("max_quote_age_seconds", DEFAULT_MAX_QUOTE_AGE_SECONDS))
        if max_age is None or max_age <= 0:
            raise QmtExecutionError("max_quote_age_seconds must be a positive finite number")
        self.max_quote_age_seconds = max_age

    def _now(self):
        value = self.now_fn()
        if not isinstance(value, datetime):
            raise QmtExecutionError("QMT bridge clock must return datetime")
        if value.tzinfo is None:
            value = value.replace(tzinfo=SHANGHAI_TZ)
        return value.astimezone(SHANGHAI_TZ)

    def _detail(self, kind):
        query = self.api.get("get_trade_detail_data")
        if not callable(query):
            try:
                import builtins

                query = getattr(builtins, "get_trade_detail_data", None)
            except (AttributeError, ImportError):
                query = None
        if not callable(query):
            raise QmtExecutionError("QMT global get_trade_detail_data is unavailable")
        account_id = self.config["account_id"]
        account_type = self.config["account_type"]
        strategy_name = self.config.get("strategy_name")
        if kind == self.config.get("position_query_type", "position") and strategy_name:
            return query(account_id, account_type, kind, strategy_name)
        return query(account_id, account_type, kind)

    def account_snapshot(self):
        account_raw = self._detail(self.config.get("account_query_type", "account"))
        accounts = _objects(
            account_raw,
            ACCOUNT_CASH_FIELDS + ACCOUNT_MARKET_VALUE_FIELDS + ACCOUNT_TOTAL_ASSET_FIELDS,
        )
        if not accounts:
            raise QmtExecutionError("QMT account query returned no object")
        account = accounts[0]
        cash = _finite_or_none(_field(account, ACCOUNT_CASH_FIELDS, None))
        if cash is None or cash < 0:
            raise QmtExecutionError("QMT account object has no usable available-cash field")
        market_value_raw = _field(account, ACCOUNT_MARKET_VALUE_FIELDS, None)
        market_value = None if market_value_raw is None else _finite_or_none(market_value_raw)
        if market_value_raw is not None and (market_value is None or market_value < 0):
            raise QmtExecutionError("QMT account object has invalid market-value field")
        total_asset_raw = _field(account, ACCOUNT_TOTAL_ASSET_FIELDS, None)
        total_asset = _finite_or_none(total_asset_raw)
        if total_asset is not None and total_asset < 0:
            total_asset = None
        frozen_raw = _field(account, ("m_dFrozenCash", "m_dFrozenBalance", "frozen_cash"), None)
        frozen_cash = 0.0 if frozen_raw is None else _finite_or_none(frozen_raw)
        if frozen_cash is None or frozen_cash < 0:
            raise QmtExecutionError("QMT account object has invalid frozen-cash field")

        holdings = []
        seen_codes = set()
        position_raw = self._detail(self.config.get("position_query_type", "position"))
        position_records = _position_records(position_raw)
        parsed_positions = 0
        for index, (position, mapping_code) in enumerate(position_records):
            code = normalize_code(_field(position, POSITION_CODE_FIELDS, mapping_code))
            shares_value = _finite_or_none(_field(position, POSITION_SHARES_FIELDS, None))
            if (
                _NORMALIZED_CODE_RE.fullmatch(code) is None
                or shares_value is None
                or shares_value < 0
                or not shares_value.is_integer()
            ):
                raise QmtExecutionError("QMT position object %d has invalid code or shares" % index)
            available_raw = _field(position, POSITION_AVAILABLE_FIELDS, None)
            available_value = 0.0 if available_raw is None else _finite_or_none(available_raw)
            if available_value is None or available_value < 0 or not available_value.is_integer():
                raise QmtExecutionError("QMT position object %d has invalid available shares" % index)
            parsed_positions += 1
            shares = int(shares_value)
            available = int(available_value)
            if available > shares:
                raise QmtExecutionError("QMT position object %d has available shares above total shares" % index)
            if shares == 0:
                continue
            if code in seen_codes:
                raise QmtExecutionError("QMT position query contains duplicate stock codes")
            seen_codes.add(code)
            last_raw = _field(position, ("m_dLastPrice", "m_dSettlementPrice", "last_price", "price"), None)
            last = 0.0 if last_raw is None else _finite_or_none(last_raw)
            if last is None or last < 0:
                raise QmtExecutionError("QMT position object has invalid last-price field")
            position_value = _field(
                position,
                ("m_dInstrumentValue", "m_dMarketValue", "market_value"),
                None,
            )
            parsed_position_value = None if position_value is None else _finite_or_none(position_value)
            if position_value is not None and (parsed_position_value is None or parsed_position_value < 0):
                raise QmtExecutionError("QMT position object has invalid market-value field")
            holdings.append(
                {
                    "code": code,
                    "shares": shares,
                    "available_shares": available,
                    "held_days": 1 if available > 0 else 0,
                    "last_price": last,
                    "market_value": shares * last if parsed_position_value is None else parsed_position_value,
                }
            )
        if position_records and parsed_positions == 0:
            raise QmtExecutionError("QMT position query was non-empty but no position object could be parsed")
        if not holdings and market_value is not None and market_value > 0.01:
            raise QmtExecutionError(
                "QMT position query is empty while account market value is positive"
            )
        if market_value is None:
            market_value = sum(row["market_value"] for row in holdings)
        return {
            "cash": cash,
            "frozen_cash": frozen_cash,
            "market_value": market_value,
            "total_asset": total_asset,
            "holdings": sorted(holdings, key=lambda row: row["code"]),
            "source": "broker_qmt",
        }

    def market_snapshot(self, codes, exec_date):
        getter = getattr(self.context_info, "get_full_tick", None)
        if not callable(getter):
            raise QmtExecutionError("ContextInfo.get_full_tick is unavailable")
        requested = [qmt_code(code) for code in codes]
        raw = getter(requested)
        if not isinstance(raw, dict):
            raise QmtExecutionError("QMT full-tick query did not return a mapping")
        result = {}
        now = self._now()
        for raw_code in requested:
            if raw_code in raw:
                tick = raw[raw_code]
            else:
                tick = raw.get(normalize_code(raw_code))
            code = normalize_code(raw_code)
            if not isinstance(tick, dict):
                result[code] = self._unavailable_quote("missing_quote")
                continue
            bid_prices = tick.get("bidPrice")
            if bid_prices is None:
                bid_prices = tick.get("bid_price")
            ask_prices = tick.get("askPrice")
            if ask_prices is None:
                ask_prices = tick.get("ask_price")
            bid1 = _number(self._first_level(bid_prices, tick.get("bid1")))
            ask1 = _number(self._first_level(ask_prices, tick.get("ask1")))
            last = _number(tick.get("lastPrice", tick.get("last", tick.get("price", 0.0))))
            high_limit = _number(tick.get("UpStopPrice", tick.get("high_limit", 0.0)))
            low_limit = _number(tick.get("DownStopPrice", tick.get("low_limit", 0.0)))
            if (high_limit <= 0 or low_limit <= 0) and hasattr(self.context_info, "get_instrumentdetail"):
                detail = self.context_info.get_instrumentdetail(raw_code) or {}
                high_limit = high_limit or _number(detail.get("UpStopPrice"))
                low_limit = low_limit or _number(detail.get("DownStopPrice"))
            timestamp_value = tick.get("timetag")
            if timestamp_value in (None, ""):
                timestamp_value = tick.get("timestamp")
            timestamp_dt = self._parse_quote_datetime(timestamp_value)
            timestamp = "" if timestamp_dt is None else timestamp_dt.isoformat()
            limits_ready = high_limit > 0 and low_limit > 0
            timestamp_ready = False
            if timestamp_dt is None:
                status = "missing_or_invalid_timestamp"
            elif timestamp_dt.date().isoformat() != exec_date:
                status = "stale_timestamp"
            else:
                age_seconds = (now - timestamp_dt).total_seconds()
                if age_seconds > self.max_quote_age_seconds:
                    status = "stale_timestamp"
                elif age_seconds < -MAX_FUTURE_QUOTE_SKEW_SECONDS:
                    status = "future_timestamp"
                else:
                    timestamp_ready = True
                    status = "normal"
            if timestamp_ready:
                if last <= 0:
                    status = "missing_last_price"
                    timestamp_ready = False
                elif not limits_ready:
                    status = "missing_limit_price"
                elif bid1 <= 0 and ask1 <= 0:
                    status = "no_orderbook"
            result[code] = {
                "timestamp": timestamp,
                "bid1": bid1,
                "ask1": ask1,
                "last": last,
                "high_limit": high_limit,
                "low_limit": low_limit,
                "buyable": timestamp_ready and limits_ready and ask1 > 0 and ask1 < high_limit,
                "sellable": timestamp_ready and limits_ready and bid1 > 0 and bid1 > low_limit,
                "status": status,
            }
        return result

    @staticmethod
    def _first_level(levels, fallback):
        if levels is None:
            return fallback
        try:
            return levels[0] if len(levels) else fallback
        except (TypeError, IndexError, KeyError):
            return fallback

    @staticmethod
    def _unavailable_quote(status):
        return {
            "timestamp": "",
            "bid1": 0.0,
            "ask1": 0.0,
            "last": 0.0,
            "high_limit": 0.0,
            "low_limit": 0.0,
            "buyable": False,
            "sellable": False,
            "status": status,
        }

    @staticmethod
    def _parse_quote_datetime(value):
        if isinstance(value, datetime):
            parsed = value
            if parsed.tzinfo is None:
                return None
            return parsed.astimezone(SHANGHAI_TZ)
        text = str(value or "").strip()
        iso_match = _ISO_TIMESTAMP_RE.match(text)
        if iso_match is not None:
            try:
                normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
                parsed = datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%S%z")
            except ValueError:
                try:
                    parsed = datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%S.%f%z")
                except ValueError:
                    return None
            return parsed.astimezone(SHANGHAI_TZ)
        if _COMPACT_TIMESTAMP_RE.match(text) is not None:
            try:
                return datetime.strptime(text[:14], "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI_TZ)
            except ValueError:
                return None
        if _EPOCH_TIMESTAMP_RE.match(text) is not None:
            try:
                seconds = int(text) / (1000.0 if len(text) == 13 else 1.0)
                return datetime.fromtimestamp(seconds, SHANGHAI_TZ)
            except (OverflowError, OSError, ValueError):
                return None
        return None

    @classmethod
    def _quote_timestamp(cls, value, exec_date):
        parsed = cls._parse_quote_datetime(value)
        return "" if parsed is None else parsed.isoformat()

    def execute_stage(self, stage, orders, wait_seconds):
        raise QmtExecutionError(
            "order submission remains locked until qmt_probe confirms Guojin order/cancel/query APIs"
        )

    def recover_stage(self, stage, orders, wait_seconds):
        return None


__all__ = ["GuojinQmtBridge"]

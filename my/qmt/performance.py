"""QMT broker-fact performance ledgers.

This module runs in the normal Windows Python environment, not inside QMT's
embedded Python.  It imports a QMT ``result.json`` and its closing broker
snapshot into three independent, idempotent CSV ledgers:

* ``qmt_nav.csv`` -- broker total assets and flow-adjusted returns;
* ``qmt_trades.csv`` -- plans, orders and real fills;
* ``qmt_cash_flows.csv`` -- broker-reported or manually registered cash flows.

Broker ``total_asset`` is deliberately the only NAV fact.  A missing or
non-broker snapshot is recorded as ``nav_status=missing`` rather than being
reconstructed from positions, fills, or shadow-mode data.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import pandas as pd


DEFAULT_PERFORMANCE_DIR = Path(__file__).resolve().parents[1] / "runtime" / "qmt_performance"
DEFAULT_RUNTIME_ROOT = Path(__file__).resolve().parents[1] / "runtime"

NAV_COLUMNS = [
    "exec_date",
    "batch_id",
    "snapshot_at",
    "nav_status",
    "execution_status",
    "cash",
    "frozen_cash",
    "market_value",
    "total_asset",
    "external_cash_flow",
    "cash_flow_source",
    "daily_return",
    "cumulative_return",
    "current_drawdown",
    "max_drawdown",
    "benchmark_daily_return",
    "benchmark_cumulative_return",
    "cumulative_excess_return",
    "anomaly",
    "note",
]

TRADE_COLUMNS = [
    "exec_date",
    "batch_id",
    "stage",
    "order_id",
    "fill_id",
    "broker_order_id",
    "code",
    "side",
    "planned_shares",
    "submitted_shares",
    "filled_shares",
    "planned_price",
    "submitted_price",
    "fill_price",
    "raw_price_slippage",
    "slippage",
    "slippage_bps",
    "commission",
    "tax",
    "transfer_fee",
    "other_fee",
    "total_fee",
    "fee_status",
    "order_status",
    "cancelled_shares",
    "cancel_status",
    "is_system_order",
    "anomaly",
]

CASH_FLOW_COLUMNS = ["exec_date", "flow_id", "amount", "source", "status", "note"]

Payload = Union[str, os.PathLike, Mapping[str, Any]]
BenchmarkInput = Union[str, os.PathLike, Mapping[str, Any], pd.Series, pd.DataFrame]


def _empty(columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=list(columns))


def _load_payload(value: Optional[Payload], kind: str) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, Mapping):
        payload = dict(value)
    else:
        path = Path(value)
        if not path.exists():
            return None
        from my.qmt.protocol import read_json

        payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError("QMT %s payload must be a JSON object" % kind)

    # Do not let a caller turn a local estimate into a broker fact merely by
    # supplying a Mapping.  Files and in-memory payloads follow the same full
    # protocol validation path, including schema, timestamps and checksum.
    from my.qmt.protocol import PLANNER_VERSION, validate_eod_snapshot, validate_result

    if kind == "result":
        validate_result(
            payload,
            expected_exec_date=payload.get("exec_date"),
            expected_planner_version=PLANNER_VERSION,
        )
    elif kind == "eod_snapshot":
        validate_eod_snapshot(payload, expected_exec_date=payload.get("exec_date"))
    else:
        raise ValueError("unknown QMT payload kind: %s" % kind)
    return payload


def _text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, (Mapping, list, tuple, set)):
        try:
            if bool(pd.isna(value)):
                return ""
        except (TypeError, ValueError):
            pass
    return str(value).strip()


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _first(record: Optional[Mapping[str, Any]], keys: Iterable[str], default: Any = None) -> Any:
    if not isinstance(record, Mapping):
        return default
    for key in keys:
        value = record.get(key)
        if value is not None and not (isinstance(value, str) and not value.strip()):
            return value
    return default


def _records(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, (str, bytes)):
        return [{"order_id": _text(value)}]
    if not isinstance(value, Sequence):
        return []
    output: List[Dict[str, Any]] = []
    for item in value:
        if isinstance(item, Mapping):
            output.append(dict(item))
        elif item is not None:
            output.append({"order_id": _text(item)})
    return output


def _read_csv(path: Path, columns: Sequence[str]) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return _empty(columns)
    try:
        frame = pd.read_csv(path, encoding="utf-8-sig")
    except pd.errors.EmptyDataError:
        return _empty(columns)
    for column in columns:
        if column not in frame.columns:
            frame[column] = pd.NA
    # Keep extra columns until the caller normalises the frame.  In
    # particular this allows a user-authored cash-flow file with ``date`` and
    # ``net_amount`` aliases to be migrated without losing those values.
    return frame.copy()


def _atomic_write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        frame.to_csv(temporary, index=False, encoding="utf-8-sig", float_format="%.12g")
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def _performance_lock(directory: Path, timeout_seconds: float = 10.0):
    """Serialize read-modify-write ledger updates across processes."""

    directory.mkdir(parents=True, exist_ok=True)
    from my.qmt.protocol import _write_lock

    # Use the same PID/token-aware lock as immutable protocol files.  A hard
    # process kill can therefore be recovered after the stale-lock window
    # instead of disabling the ledger forever.
    with _write_lock(directory / ".qmt_performance", timeout_seconds=timeout_seconds):
        yield


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _normalise_date(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        raise ValueError("invalid exec_date: %r" % value)
    return parsed.strftime("%Y-%m-%d")


def _join_messages(values: Iterable[Any]) -> str:
    messages: List[str] = []
    for value in values:
        if value is None or value == "":
            continue
        if isinstance(value, str):
            message = value.strip()
        else:
            message = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if message and message not in messages:
            messages.append(message)
    return " | ".join(messages)


def _order_id(record: Mapping[str, Any]) -> str:
    return _text(_first(record, ("order_id", "client_order_id", "local_order_id", "order_remark")))


def _broker_order_id(record: Mapping[str, Any]) -> str:
    return _text(
        _first(record, ("broker_order_id", "entrust_no", "entrust_id", "broker_id", "order_sys_id"))
    )


def _fill_id(record: Mapping[str, Any], stage: str, order_id: str, ordinal: int) -> str:
    explicit = _text(
        _first(record, ("fill_id", "trade_id", "deal_id", "broker_fill_id", "trade_no", "deal_no"))
    )
    if explicit:
        return explicit
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return "auto:%s:%s:%d:%s" % (stage, order_id or "unknown", ordinal, digest)


def _normalise_side(value: Any) -> str:
    side = _text(value).lower()
    if side in {"b", "buy", "23", "1", "买", "买入"}:
        return "buy"
    if side in {"s", "sell", "24", "2", "卖", "卖出"}:
        return "sell"
    return side


def _extract_fee(
    fill: Mapping[str, Any],
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float], str]:
    nested = fill.get("fees") if isinstance(fill.get("fees"), Mapping) else {}

    def field(*names: str) -> Optional[float]:
        value = _first(fill, names)
        if value is None:
            value = _first(nested, names)
        return _number(value)

    commission = field("commission", "commission_fee", "brokerage")
    tax = field("tax", "tax_fee", "stamp_tax")
    transfer_fee = field("transfer_fee")
    other_fee = field("other_fee", "misc_fee")
    total = field("total_fee", "fee", "fees_total", "trade_fee", "cost")
    if total is None and not isinstance(fill.get("fees"), Mapping):
        total = _number(fill.get("fees"))
    explicit_status = _text(_first(fill, ("fee_status",)))

    components = [commission, tax, transfer_fee, other_fee]
    if total is not None:
        status = explicit_status or "known"
    elif all(value is not None for value in components):
        total = float(sum(value for value in components if value is not None))
        status = explicit_status or "known"
    elif any(value is not None for value in components):
        # Missing fee components are not silently treated as zero.
        status = explicit_status or "partial"
    else:
        status = explicit_status or "unknown"
    return commission, tax, transfer_fee, other_fee, total, status


def _merge_value(*records_and_keys: Any) -> Any:
    """Return the first populated value from ``(record, keys)`` pairs."""

    for record, keys in records_and_keys:
        value = _first(record, keys)
        if value is not None and value != "":
            return value
    return None


def _trade_rows_for_stage(result: Mapping[str, Any], stage_name: str) -> List[Dict[str, Any]]:
    stage_payload = result.get("%s_stage" % stage_name)
    if not isinstance(stage_payload, Mapping):
        return []

    planned = _records(stage_payload.get("planned"))
    orders = _records(stage_payload.get("broker_orders"))
    fills = _records(stage_payload.get("fills"))
    cancelled = _records(stage_payload.get("cancelled"))

    # Some adapters place fills below their broker order object.
    for order in orders:
        for nested_fill in _records(order.get("fills")):
            nested_fill.setdefault("order_id", _order_id(order))
            nested_fill.setdefault("broker_order_id", _broker_order_id(order))
            fills.append(nested_fill)

    plans_by_order = {_order_id(item): item for item in planned if _order_id(item)}
    broker_to_local: Dict[str, str] = {}
    orders_by_order: Dict[str, Dict[str, Any]] = {}
    for order in orders:
        local_id = _order_id(order)
        broker_id = _broker_order_id(order)
        if broker_id and local_id:
            broker_to_local[broker_id] = local_id

    def resolve_local(record: Mapping[str, Any], source: str, ordinal: int) -> str:
        local_id = _order_id(record)
        broker_id = _broker_order_id(record)
        if local_id in plans_by_order or local_id in orders_by_order:
            return local_id
        if broker_id in broker_to_local:
            return broker_to_local[broker_id]
        if local_id in broker_to_local:
            return broker_to_local[local_id]
        if local_id:
            return local_id
        if broker_id:
            return "unmatched:broker:%s" % broker_id
        canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
        return "unmatched:%s:%d:%s" % (source, ordinal, digest)

    for ordinal, order in enumerate(orders, start=1):
        orders_by_order[resolve_local(order, "order", ordinal)] = order

    cancels_by_order: Dict[str, Dict[str, Any]] = {}
    for ordinal, cancel in enumerate(cancelled, start=1):
        local_id = resolve_local(cancel, "cancel", ordinal)
        if local_id:
            cancels_by_order[local_id] = cancel

    fills_by_order: Dict[str, List[Dict[str, Any]]] = {}
    for ordinal, fill in enumerate(fills, start=1):
        fills_by_order.setdefault(resolve_local(fill, "fill", ordinal), []).append(fill)

    all_order_ids: List[str] = []
    for source, collection in (
        ("plan", planned),
        ("order", orders),
        ("cancel", cancelled),
        ("fill", fills),
    ):
        for ordinal, item in enumerate(collection, start=1):
            local_id = resolve_local(item, source, ordinal)
            if local_id not in all_order_ids:
                all_order_ids.append(local_id)

    exec_date = _normalise_date(result.get("exec_date"))
    batch_id = _text(result.get("batch_id"))
    rows: List[Dict[str, Any]] = []
    for local_id in all_order_ids:
        plan = plans_by_order.get(local_id, {})
        order = orders_by_order.get(local_id, {})
        cancel = cancels_by_order.get(local_id, {})
        order_fills = fills_by_order.get(local_id) or [None]
        for ordinal, fill_or_none in enumerate(order_fills, start=1):
            fill = fill_or_none or {}
            code = _text(
                _merge_value(
                    (fill, ("code", "stock_code", "symbol", "instrument")),
                    (order, ("code", "stock_code", "symbol", "instrument")),
                    (plan, ("code", "stock_code", "symbol", "instrument")),
                )
            )
            side = _normalise_side(
                _merge_value(
                    (fill, ("side", "action", "direction")),
                    (order, ("side", "action", "direction")),
                    (plan, ("side", "action", "direction")),
                )
            )
            planned_price = _number(
                _merge_value(
                    (plan, ("planned_price", "limit_price", "reference_price", "ref_price", "price")),
                    (order, ("planned_price", "reference_price", "ref_price")),
                )
            )
            submitted_price = _number(
                _merge_value(
                    (order, ("submitted_price", "submit_price", "order_price", "limit_price", "price")),
                    (plan, ("submitted_price", "submit_price", "limit_price", "price")),
                )
            )
            fill_price = _number(_first(fill, ("fill_price", "trade_price", "deal_price", "price", "avg_price")))
            raw_slippage: Optional[float] = None
            adverse_slippage: Optional[float] = None
            if planned_price not in (None, 0.0) and fill_price is not None:
                raw_slippage = fill_price / planned_price - 1.0
                adverse_slippage = -raw_slippage if side == "sell" else raw_slippage

            commission, tax, transfer_fee, other_fee, total_fee, fee_status = _extract_fee(fill)
            has_fill = fill_or_none is not None
            system_order = bool(local_id and local_id in plans_by_order)
            row_anomaly = "" if system_order else "unknown_order_id"

            rows.append(
                {
                    "exec_date": exec_date,
                    "batch_id": batch_id,
                    "stage": stage_name,
                    "order_id": local_id,
                    "fill_id": _fill_id(fill, stage_name, local_id, ordinal) if has_fill else "",
                    "broker_order_id": _text(
                        _merge_value(
                            (fill, ("broker_order_id", "entrust_no", "entrust_id", "broker_id", "order_sys_id")),
                            (order, ("broker_order_id", "entrust_no", "entrust_id", "broker_id", "order_sys_id")),
                        )
                    ),
                    "code": code,
                    "side": side,
                    "planned_shares": _number(
                        _first(plan, ("planned_shares", "shares", "quantity", "qty", "volume"))
                    ),
                    "submitted_shares": _number(
                        _merge_value(
                            (order, ("submitted_shares", "shares", "quantity", "qty", "volume")),
                            (plan, ("shares", "quantity", "qty", "volume")),
                        )
                    ),
                    "filled_shares": _number(
                        _first(fill, ("filled_shares", "shares", "quantity", "qty", "volume", "deal_volume"))
                    ),
                    "planned_price": planned_price,
                    "submitted_price": submitted_price,
                    "fill_price": fill_price,
                    "raw_price_slippage": raw_slippage,
                    "slippage": adverse_slippage,
                    "slippage_bps": adverse_slippage * 10000.0 if adverse_slippage is not None else None,
                    "commission": commission,
                    "tax": tax,
                    "transfer_fee": transfer_fee,
                    "other_fee": other_fee,
                    "total_fee": total_fee,
                    "fee_status": fee_status if has_fill else "not_applicable",
                    "order_status": _text(_first(order, ("status", "order_status", "state"))),
                    "cancelled_shares": _number(
                        _first(cancel, ("cancelled_shares", "shares", "quantity", "qty", "volume"))
                    ),
                    "cancel_status": _text(
                        _first(cancel, ("status", "cancel_status", "state"), "cancelled" if cancel else "")
                    ),
                    "is_system_order": system_order,
                    "anomaly": row_anomaly,
                }
            )
    return rows


def _update_trades(result: Optional[Mapping[str, Any]], path: Path) -> pd.DataFrame:
    existing = _read_csv(path, TRADE_COLUMNS)
    if result is None:
        _atomic_write_csv(path, existing)
        return existing

    batch_id = _text(result.get("batch_id"))
    if not batch_id:
        raise ValueError("result.json is missing batch_id")
    exec_date = _normalise_date(result.get("exec_date"))
    if not existing.empty:
        # There is exactly one production batch per execution date.  If a
        # corrected batch replaces that date, its old fills must not survive.
        existing = existing[
            (existing["batch_id"].fillna("").astype(str) != batch_id)
            & (existing["exec_date"].fillna("").astype(str) != exec_date)
        ]

    rows = _trade_rows_for_stage(result, "sell") + _trade_rows_for_stage(result, "buy")
    additions = pd.DataFrame(rows, columns=TRADE_COLUMNS)
    if existing.empty:
        combined = additions.copy()
    elif additions.empty:
        combined = existing.copy()
    else:
        combined = pd.DataFrame(
            existing.reindex(columns=TRADE_COLUMNS).to_dict("records")
            + additions.reindex(columns=TRADE_COLUMNS).to_dict("records"),
            columns=TRADE_COLUMNS,
        )
    if not combined.empty:
        keys = ["batch_id", "order_id", "fill_id"]
        for key in keys:
            combined[key] = combined[key].fillna("").astype(str)
        combined = combined.drop_duplicates(subset=keys, keep="last")
        combined = combined.sort_values(
            ["exec_date", "batch_id", "stage", "order_id", "fill_id"], kind="mergesort"
        ).reset_index(drop=True)
    combined = combined.reindex(columns=TRADE_COLUMNS)
    _atomic_write_csv(path, combined)
    return combined


def _normalise_cash_flows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return _empty(CASH_FLOW_COLUMNS)
    if "date" in frame.columns and (
        "exec_date" not in frame.columns or not frame["exec_date"].map(_text).any()
    ):
        frame["exec_date"] = frame["date"]
    if "amount" not in frame.columns or not pd.to_numeric(frame["amount"], errors="coerce").notna().any():
        for candidate in ("external_cash_flow", "net_amount", "cash_flow"):
            if candidate in frame.columns:
                frame["amount"] = frame[candidate]
                break
    frame = frame.copy()
    for column in CASH_FLOW_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.NA
    frame = frame[CASH_FLOW_COLUMNS]
    frame["exec_date"] = frame["exec_date"].map(lambda value: _normalise_date(value) if _text(value) else "")
    invalid_amounts = frame["amount"].map(lambda value: _number(value) is None)
    if invalid_amounts.any():
        identifiers = [
            _text(value) or "<missing-flow-id>" for value in frame.loc[invalid_amounts, "flow_id"].tolist()
        ]
        raise ValueError("cash-flow ledger contains invalid amount: %s" % ", ".join(identifiers))
    parsed_amounts = frame["amount"].map(_number)
    frame["amount"] = parsed_amounts.astype(float)
    frame["source"] = frame["source"].fillna("manual").replace("", "manual")
    frame["status"] = frame["status"].fillna("confirmed").replace("", "confirmed")
    for ordinal, index in enumerate(frame.index, start=1):
        if not _text(frame.at[index, "flow_id"]):
            raw = "%s|%s|%s" % (frame.at[index, "exec_date"], frame.at[index, "amount"], ordinal)
            frame.at[index, "flow_id"] = "manual:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return frame


def _flow_number(value: Any) -> Optional[float]:
    direct = _number(value)
    if direct is not None:
        return direct
    if not isinstance(value, Mapping):
        return None
    direct = _number(_first(value, ("net", "net_amount", "amount", "external_cash_flow")))
    if direct is not None:
        return direct
    inflow = _number(_first(value, ("inflow", "deposit", "cash_in")))
    outflow = _number(_first(value, ("outflow", "withdrawal", "cash_out")))
    if inflow is not None or outflow is not None:
        return (inflow or 0.0) - (outflow or 0.0)
    return None


def _update_cash_flows(
    eod: Optional[Mapping[str, Any]], exec_date: str, batch_id: str, path: Path
) -> Tuple[pd.DataFrame, float, str, str]:
    existing_raw = _read_csv(path, CASH_FLOW_COLUMNS)
    existing = _normalise_cash_flows(existing_raw)
    trusted_eod = eod is not None and _text(eod.get("source")) == "broker_qmt"
    broker_flow = _flow_number(eod.get("external_cash_flow")) if trusted_eod else None
    broker_flow_id = "broker:%s" % (batch_id or exec_date)
    if eod is not None:
        existing = existing[
            ~(
                (existing["exec_date"].fillna("").astype(str) == exec_date)
                & (existing["source"].fillna("").astype(str) == "broker_qmt")
            )
        ]
        if broker_flow is not None:
            broker_row = pd.DataFrame(
                [
                    {
                        "exec_date": exec_date,
                        "flow_id": broker_flow_id,
                        "amount": broker_flow,
                        "source": "broker_qmt",
                        "status": "confirmed",
                        "note": "eod_snapshot.external_cash_flow",
                    }
                ],
                columns=CASH_FLOW_COLUMNS,
            )
            existing = broker_row if existing.empty else pd.concat([existing, broker_row], ignore_index=True)

    if not existing.empty:
        existing["flow_id"] = existing["flow_id"].fillna("").astype(str)
        existing = existing.drop_duplicates(subset=["flow_id"], keep="last")
        existing = existing.sort_values(["exec_date", "flow_id"], kind="mergesort").reset_index(drop=True)
    existing = existing.reindex(columns=CASH_FLOW_COLUMNS)
    _atomic_write_csv(path, existing)

    manual_for_day = existing[
        (existing["exec_date"].astype(str) == exec_date)
        & (existing["source"].fillna("").astype(str) != "broker_qmt")
        & (existing["status"].fillna("confirmed").astype(str) != "void")
    ]
    manual_total = float(pd.to_numeric(manual_for_day["amount"], errors="coerce").fillna(0.0).sum())
    anomaly = ""
    if broker_flow is not None:
        if not manual_for_day.empty and abs(manual_total) > 1e-12:
            anomaly = "manual_cash_flow_ignored_broker_preferred"
        return existing, broker_flow, "broker_qmt", anomaly
    if not manual_for_day.empty:
        return existing, manual_total, "manual", anomaly
    return (
        existing,
        0.0,
        "assumed_zero_dedicated_account",
        "external_cash_flow_unavailable_assumed_zero_dedicated_account",
    )


def register_cash_flow(
    exec_date: str,
    amount: float,
    output_dir: Union[str, os.PathLike] = DEFAULT_PERFORMANCE_DIR,
    *,
    flow_id: Optional[str] = None,
    note: str = "",
) -> pd.DataFrame:
    """Idempotently register a manual deposit (positive) or withdrawal (negative)."""

    date = _normalise_date(exec_date)
    numeric = _number(amount)
    if numeric is None:
        raise ValueError("cash-flow amount must be finite")
    directory = Path(output_dir)
    path = directory / "qmt_cash_flows.csv"
    with _performance_lock(directory):
        frame = _normalise_cash_flows(_read_csv(path, CASH_FLOW_COLUMNS))
        identifier = flow_id or "manual:%s:%s" % (
            date,
            hashlib.sha256((str(numeric) + note).encode("utf-8")).hexdigest()[:12],
        )
        frame = frame[frame["flow_id"].fillna("").astype(str) != identifier]
        addition = pd.DataFrame(
            [
                {
                    "exec_date": date,
                    "flow_id": identifier,
                    "amount": numeric,
                    "source": "manual",
                    "status": "confirmed",
                    "note": note,
                }
            ],
            columns=CASH_FLOW_COLUMNS,
        )
        frame = (addition if frame.empty else pd.concat([frame, addition], ignore_index=True)).sort_values(
            ["exec_date", "flow_id"], kind="mergesort"
        )
        frame = frame.reset_index(drop=True).reindex(columns=CASH_FLOW_COLUMNS)
        _atomic_write_csv(path, frame)
        return frame


def _benchmark_mapping(value: Optional[BenchmarkInput]) -> Dict[str, float]:
    if value is None:
        return {}
    if isinstance(value, pd.Series):
        return {
            _normalise_date(index): numeric
            for index, raw in value.items()
            if (numeric := _number(raw)) is not None
        }
    if isinstance(value, pd.DataFrame):
        frame = value.copy()
    elif isinstance(value, Mapping):
        if all(_number(raw) is not None for raw in value.values()):
            return {_normalise_date(key): float(_number(raw)) for key, raw in value.items()}
        if all(isinstance(raw, Mapping) for raw in value.values()):
            output: Dict[str, float] = {}
            for key, raw in value.items():
                numeric = _number(_first(raw, ("benchmark_daily_return", "daily_return", "return")))
                if numeric is not None:
                    output[_normalise_date(key)] = numeric
            return output
        frame = pd.DataFrame(value)
    else:
        path = Path(value)
        if path.suffix.lower() == ".json":
            with path.open("r", encoding="utf-8-sig") as stream:
                return _benchmark_mapping(json.load(stream))
        frame = pd.read_csv(path, encoding="utf-8-sig")

    date_column = next((name for name in ("exec_date", "date", "trade_date") if name in frame.columns), None)
    return_column = next(
        (name for name in ("benchmark_daily_return", "daily_return", "return") if name in frame.columns), None
    )
    if date_column is None or return_column is None:
        raise ValueError("benchmark data needs a date column and a daily-return column")
    output: Dict[str, float] = {}
    for _, row in frame.iterrows():
        numeric = _number(row[return_column])
        if numeric is not None:
            output[_normalise_date(row[date_column])] = numeric
    return output


def _eod_benchmark_return(eod: Optional[Mapping[str, Any]]) -> Optional[float]:
    if eod is None:
        return None
    value = _number(eod.get("benchmark_daily_return"))
    if value is not None:
        return value
    benchmark = eod.get("benchmark")
    if isinstance(benchmark, Mapping):
        return _number(_first(benchmark, ("daily_return", "return")))
    return None


def _validate_payload_identity(
    result: Optional[Mapping[str, Any]], eod: Optional[Mapping[str, Any]]
) -> None:
    """Validate cross-file identity before any ledger is mutated."""

    result_date = _normalise_date(result.get("exec_date")) if result is not None else ""
    eod_date = _normalise_date(eod.get("exec_date")) if eod is not None else ""
    if result is not None and not result_date:
        raise ValueError("result.json is missing exec_date")
    if eod is not None and not eod_date:
        raise ValueError("eod_snapshot.json is missing exec_date")
    if result_date and eod_date and result_date != eod_date:
        raise ValueError("result/eod exec_date mismatch: %s != %s" % (result_date, eod_date))

    result_batch = _text(result.get("batch_id")) if result is not None else ""
    eod_batch = _text(eod.get("batch_id")) if eod is not None else ""
    if result is not None and not result_batch:
        raise ValueError("result.json is missing batch_id")
    if eod is not None and not eod_batch:
        raise ValueError("eod_snapshot.json is missing batch_id")
    if result_batch and eod_batch and result_batch != eod_batch:
        raise ValueError("result/eod batch_id mismatch: %s != %s" % (result_batch, eod_batch))


def _validate_partial_batch_replacement(
    result: Optional[Mapping[str, Any]],
    eod: Optional[Mapping[str, Any]],
    nav_path: Path,
    trade_path: Path,
) -> None:
    """Never combine one half of a new batch with an older same-day batch."""

    if result is not None and eod is not None:
        return
    payload = result if result is not None else eod
    if payload is None:
        return
    exec_date = _normalise_date(payload.get("exec_date"))
    batch_id = _text(payload.get("batch_id"))
    existing_batches = set()
    nav = _read_csv(nav_path, NAV_COLUMNS)
    if not nav.empty:
        rows = nav[nav["exec_date"].fillna("").astype(str) == exec_date]
        existing_batches.update(_text(value) for value in rows["batch_id"] if _text(value))
    trades = _read_csv(trade_path, TRADE_COLUMNS)
    if not trades.empty:
        rows = trades[trades["exec_date"].fillna("").astype(str) == exec_date]
        existing_batches.update(_text(value) for value in rows["batch_id"] if _text(value))
    incompatible = sorted(existing_batches.difference({batch_id}))
    if incompatible:
        raise ValueError(
            "refusing partial same-day batch replacement: incoming=%s existing=%s; "
            "provide matching result.json and eod_snapshot.json together"
            % (batch_id, ",".join(incompatible))
        )


def _recompute_nav(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return _empty(NAV_COLUMNS)
    frame = frame.reindex(columns=NAV_COLUMNS).copy()
    frame["exec_date"] = frame["exec_date"].map(_normalise_date)
    frame = frame.drop_duplicates(subset=["exec_date"], keep="last")
    frame = frame.sort_values("exec_date", kind="mergesort").reset_index(drop=True)
    generated_anomaly_prefixes = (
        "return_spans_",
        "return_unavailable_previous_total_asset_zero",
    )
    frame["anomaly"] = frame["anomaly"].map(
        lambda value: " | ".join(
            part
            for part in _text(value).split(" | ")
            if part and not part.startswith(generated_anomaly_prefixes)
        )
    )
    numeric_columns = [
        "cash",
        "frozen_cash",
        "market_value",
        "total_asset",
        "external_cash_flow",
        "benchmark_daily_return",
    ]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    daily: List[Optional[float]] = []
    cumulative: List[Optional[float]] = []
    drawdowns: List[Optional[float]] = []
    max_drawdowns: List[Optional[float]] = []
    benchmark_cumulative: List[Optional[float]] = []
    excess: List[Optional[float]] = []
    first_valid_seen = False
    last_valid_asset: Optional[float] = None
    last_valid_cumulative: Optional[float] = None
    last_valid_benchmark_cumulative: Optional[float] = None
    pending_flow = 0.0
    pending_benchmark_factor = 1.0
    pending_benchmark_complete = True
    missing_nav_rows = 0
    equity_peak: Optional[float] = None
    running_max_drawdown: Optional[float] = None

    for index, row in frame.iterrows():
        asset = _number(row["total_asset"]) if _text(row["nav_status"]) == "ok" else None
        flow = _number(row["external_cash_flow"]) or 0.0
        benchmark_daily = _number(row["benchmark_daily_return"])

        # Once the first broker NAV exists, retain the last valid fact across
        # missing rows.  The next valid observation represents a multi-day
        # period, including every external flow and benchmark return in it.
        if first_valid_seen:
            if benchmark_daily is None:
                pending_benchmark_complete = False
            else:
                pending_benchmark_factor *= 1.0 + benchmark_daily

        if asset is None:
            day_return = None
            cumulative_return = None
            benchmark_cum = None
            if first_valid_seen:
                pending_flow += flow
                missing_nav_rows += 1
        elif not first_valid_seen:
            day_return = 0.0
            cumulative_return = 0.0
            benchmark_cum = 0.0 if benchmark_daily is not None else None
            first_valid_seen = True
            last_valid_asset = asset
            last_valid_cumulative = cumulative_return
            last_valid_benchmark_cumulative = benchmark_cum
            pending_flow = 0.0
            pending_benchmark_factor = 1.0
            pending_benchmark_complete = True
        else:
            if last_valid_asset in (None, 0.0):
                day_return = None
                cumulative_return = None
                frame.at[index, "anomaly"] = _join_messages(
                    [frame.at[index, "anomaly"], "return_unavailable_previous_total_asset_zero"]
                )
            else:
                day_return = (asset - pending_flow - flow) / last_valid_asset - 1.0
                cumulative_return = (
                    (1.0 + last_valid_cumulative) * (1.0 + day_return) - 1.0
                    if last_valid_cumulative is not None
                    else None
                )
            if (
                last_valid_benchmark_cumulative is not None
                and pending_benchmark_complete
            ):
                benchmark_cum = (
                    (1.0 + last_valid_benchmark_cumulative) * pending_benchmark_factor - 1.0
                )
            else:
                benchmark_cum = None
            if missing_nav_rows:
                frame.at[index, "anomaly"] = _join_messages(
                    [
                        frame.at[index, "anomaly"],
                        "return_spans_%d_missing_nav_row%s"
                        % (missing_nav_rows, "" if missing_nav_rows == 1 else "s"),
                    ]
                )
            last_valid_asset = asset
            last_valid_cumulative = cumulative_return
            last_valid_benchmark_cumulative = benchmark_cum
            pending_flow = 0.0
            pending_benchmark_factor = 1.0
            pending_benchmark_complete = True
            missing_nav_rows = 0

        daily.append(day_return)
        cumulative.append(cumulative_return)

        if cumulative_return is None:
            drawdown = None
        else:
            equity = 1.0 + cumulative_return
            equity_peak = equity if equity_peak is None else max(equity_peak, equity)
            drawdown = equity / equity_peak - 1.0 if equity_peak else None
            if drawdown is not None:
                running_max_drawdown = (
                    drawdown if running_max_drawdown is None else min(running_max_drawdown, drawdown)
                )
        drawdowns.append(drawdown)
        max_drawdowns.append(running_max_drawdown)

        benchmark_cumulative.append(benchmark_cum)
        excess.append(
            cumulative_return - benchmark_cum
            if cumulative_return is not None and benchmark_cum is not None
            else None
        )

    frame["daily_return"] = daily
    frame["cumulative_return"] = cumulative
    frame["current_drawdown"] = drawdowns
    frame["max_drawdown"] = max_drawdowns
    frame["benchmark_cumulative_return"] = benchmark_cumulative
    frame["cumulative_excess_return"] = excess
    return frame[NAV_COLUMNS]


def _update_nav(
    result: Optional[Mapping[str, Any]],
    eod: Optional[Mapping[str, Any]],
    path: Path,
    cash_flow_path: Path,
    trade_frame: pd.DataFrame,
    benchmark_returns: Mapping[str, float],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    existing = _read_csv(path, NAV_COLUMNS)
    result_date = _normalise_date(result.get("exec_date")) if result is not None else ""
    eod_date = _normalise_date(eod.get("exec_date")) if eod is not None else ""
    if result_date and eod_date and result_date != eod_date:
        raise ValueError("result/eod exec_date mismatch: %s != %s" % (result_date, eod_date))
    exec_date = eod_date or result_date
    if not exec_date:
        cash_flows = _normalise_cash_flows(_read_csv(cash_flow_path, CASH_FLOW_COLUMNS))
        _atomic_write_csv(cash_flow_path, cash_flows)
        recomputed = _recompute_nav(existing)
        _atomic_write_csv(path, recomputed)
        return recomputed, cash_flows

    result_batch = _text(result.get("batch_id")) if result is not None else ""
    eod_batch = _text(eod.get("batch_id")) if eod is not None else ""
    if result_batch and eod_batch and result_batch != eod_batch:
        raise ValueError("result/eod batch_id mismatch: %s != %s" % (result_batch, eod_batch))
    batch_id = eod_batch or result_batch
    cash_flows, flow, flow_source, flow_anomaly = _update_cash_flows(
        eod, exec_date, batch_id, cash_flow_path
    )

    date_mask = (
        existing["exec_date"].fillna("").astype(str) == exec_date if not existing.empty else pd.Series([], dtype=bool)
    )
    prior: Dict[str, Any] = {}
    if not existing.empty and date_mask.any():
        prior = existing.loc[date_mask].iloc[-1].to_dict()

    errors = _records(result.get("errors")) if result is not None else []
    error_texts: List[Any] = list(errors)
    result_reason = _text(result.get("reason")) if result is not None else ""
    trade_anomalies: List[str] = []
    if not trade_frame.empty and batch_id:
        batch_trades = trade_frame[trade_frame["batch_id"].fillna("").astype(str) == batch_id]
        trade_anomalies = [
            _text(value) for value in batch_trades["anomaly"].tolist() if _text(value)
        ]

    if eod is None and prior:
        # Importing result.json again must not erase an already imported EOD fact.
        row = dict(prior)
        row["batch_id"] = batch_id or row.get("batch_id", "")
        row["execution_status"] = (
            _text(result.get("status")) if result is not None else _text(row.get("execution_status"))
        )
        row["anomaly"] = _join_messages([row.get("anomaly"), *error_texts, *trade_anomalies])
        if result_reason:
            row["note"] = _join_messages([row.get("note"), result_reason])
    else:
        source = _text(eod.get("source")) if eod is not None else ""
        total_asset = _number(eod.get("total_asset")) if eod is not None else None
        missing_reasons: List[str] = []
        if eod is None:
            missing_reasons.append("eod_snapshot_missing")
        elif source != "broker_qmt":
            missing_reasons.append("invalid_eod_source:%s" % (source or "empty"))
            total_asset = None
        elif total_asset is None:
            missing_reasons.append("broker_total_asset_missing")

        benchmark_daily = benchmark_returns.get(exec_date)
        if benchmark_daily is None:
            benchmark_daily = _eod_benchmark_return(eod)
        if benchmark_daily is None and prior:
            benchmark_daily = _number(prior.get("benchmark_daily_return"))

        row = {
            "exec_date": exec_date,
            "batch_id": batch_id,
            "snapshot_at": _text(eod.get("snapshot_at")) if eod is not None else "",
            "nav_status": "ok" if total_asset is not None else "missing",
            "execution_status": (
                _text(result.get("status"))
                if result is not None
                else _text(prior.get("execution_status"))
            ),
            "cash": _number(eod.get("cash")) if eod is not None and source == "broker_qmt" else None,
            "frozen_cash": (
                _number(eod.get("frozen_cash")) if eod is not None and source == "broker_qmt" else None
            ),
            "market_value": (
                _number(eod.get("market_value")) if eod is not None and source == "broker_qmt" else None
            ),
            "total_asset": total_asset,
            "external_cash_flow": flow,
            "cash_flow_source": flow_source,
            "daily_return": None,
            "cumulative_return": None,
            "current_drawdown": None,
            "max_drawdown": None,
            "benchmark_daily_return": benchmark_daily,
            "benchmark_cumulative_return": None,
            "cumulative_excess_return": None,
            "anomaly": _join_messages(
                [prior.get("anomaly") if result is None else "", flow_anomaly, *error_texts, *trade_anomalies]
            ),
            "note": _join_messages(
                [prior.get("note") if result is None else "", *missing_reasons, result_reason]
            ),
        }

    if not existing.empty:
        existing = existing[existing["exec_date"].fillna("").astype(str) != exec_date]
    addition = pd.DataFrame([row], columns=NAV_COLUMNS)
    combined = (
        addition
        if existing.empty
        else pd.DataFrame(
            existing.reindex(columns=NAV_COLUMNS).to_dict("records") + addition.to_dict("records"),
            columns=NAV_COLUMNS,
        )
    )
    # A supplied benchmark series may backfill previously imported dates.
    for index in combined.index:
        date = _normalise_date(combined.at[index, "exec_date"])
        if date in benchmark_returns:
            combined.at[index, "benchmark_daily_return"] = benchmark_returns[date]
    combined = _recompute_nav(combined)
    _atomic_write_csv(path, combined)
    return combined, cash_flows


def _report_number(value: Any, *, money: bool = False, percent: bool = False) -> str:
    numeric = _number(value)
    if numeric is None:
        return "missing"
    if percent:
        return "%.2f%%" % (numeric * 100.0)
    if money:
        return format(numeric, ",.2f")
    return "%.4f" % numeric


def _report_table(
    frame: pd.DataFrame,
    columns: Sequence[str],
    labels: Mapping[str, str],
    *,
    limit: int = 20,
) -> str:
    available = [column for column in columns if column in frame.columns]
    if frame.empty or not available:
        return '<p class="empty">暂无记录</p>'
    display = frame.tail(limit)[available].copy()
    money_columns = {"cash", "market_value", "total_asset", "external_cash_flow", "fill_price", "total_fee"}
    percent_columns = {
        "daily_return",
        "cumulative_return",
        "current_drawdown",
        "max_drawdown",
        "benchmark_daily_return",
        "benchmark_cumulative_return",
        "cumulative_excess_return",
    }
    for column in available:
        if column in money_columns:
            display[column] = display[column].map(lambda value: _report_number(value, money=True))
        elif column in percent_columns:
            display[column] = display[column].map(lambda value: _report_number(value, percent=True))
        else:
            display[column] = display[column].map(lambda value: "missing" if not _text(value) else _text(value))
    display = display.rename(columns=dict(labels))
    return display.to_html(index=False, border=0, classes=["data-table"], na_rep="missing", escape=True)


def generate_report(nav: pd.DataFrame, trades: pd.DataFrame, path: Union[str, os.PathLike]) -> Path:
    """Atomically generate a self-contained, broker-fact QMT HTML report."""

    report_path = Path(path)
    latest = nav.iloc[-1] if not nav.empty else pd.Series(dtype=object)
    nav_status = _text(latest.get("nav_status")) or "missing"
    total_asset = latest.get("total_asset") if nav_status == "ok" else None

    if trades.empty:
        fill_rows = trades
    else:
        fill_ids = trades["fill_id"].fillna("").astype(str).str.strip()
        filled_shares = pd.to_numeric(trades["filled_shares"], errors="coerce")
        fill_rows = trades[(fill_ids != "") & (filled_shares.fillna(0.0) > 0.0)]
    known_fees = pd.to_numeric(fill_rows.get("total_fee", pd.Series(dtype=float)), errors="coerce")
    known_fee_total = float(known_fees.dropna().sum()) if not known_fees.empty else 0.0
    unknown_fee_count = int(known_fees.isna().sum()) if not known_fees.empty else 0

    metrics = [
        ("最新总资产", _report_number(total_asset, money=True)),
        ("累计收益", _report_number(latest.get("cumulative_return"), percent=True)),
        ("当前回撤", _report_number(latest.get("current_drawdown"), percent=True)),
        ("最大回撤", _report_number(latest.get("max_drawdown"), percent=True)),
        ("基准累计收益", _report_number(latest.get("benchmark_cumulative_return"), percent=True)),
        ("累计超额", _report_number(latest.get("cumulative_excess_return"), percent=True)),
        ("成交数", str(len(fill_rows))),
        ("已知费用", _report_number(known_fee_total, money=True)),
        ("未知费用数", str(unknown_fee_count)),
    ]
    cards = "\n".join(
        '<div class="metric"><span>%s</span><strong>%s</strong></div>'
        % (html.escape(label), html.escape(value))
        for label, value in metrics
    )
    missing_warning = ""
    if nav_status != "ok" or _number(total_asset) is None:
        missing_warning = (
            '<div class="warning"><strong>missing</strong>：缺少可靠的券商 total_asset，'
            "本报告没有使用影子账户、持仓估值或成交记录推算净值。</div>"
        )

    nav_table = _report_table(
        nav,
        [
            "exec_date",
            "nav_status",
            "total_asset",
            "external_cash_flow",
            "daily_return",
            "cumulative_return",
            "current_drawdown",
            "max_drawdown",
            "benchmark_cumulative_return",
            "cumulative_excess_return",
            "anomaly",
            "note",
        ],
        {
            "exec_date": "日期",
            "nav_status": "净值状态",
            "total_asset": "总资产",
            "external_cash_flow": "外部资金流",
            "daily_return": "日收益",
            "cumulative_return": "累计收益",
            "current_drawdown": "当前回撤",
            "max_drawdown": "最大回撤",
            "benchmark_cumulative_return": "基准累计收益",
            "cumulative_excess_return": "累计超额",
            "anomaly": "异常",
            "note": "说明",
        },
    )
    trade_table = _report_table(
        trades,
        [
            "exec_date",
            "stage",
            "code",
            "side",
            "filled_shares",
            "fill_price",
            "total_fee",
            "fee_status",
            "slippage_bps",
            "order_status",
            "anomaly",
        ],
        {
            "exec_date": "日期",
            "stage": "阶段",
            "code": "代码",
            "side": "方向",
            "filled_shares": "成交股数",
            "fill_price": "成交价",
            "total_fee": "费用",
            "fee_status": "费用状态",
            "slippage_bps": "滑点(bps)",
            "order_status": "委托状态",
            "anomaly": "异常",
        },
    )
    latest_date = html.escape(_text(latest.get("exec_date")) or "missing")
    document = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QMT真实绩效报告</title>
<style>
:root { color-scheme: light; --ink:#18212f; --muted:#667085; --line:#e4e7ec; --panel:#f8fafc; }
* { box-sizing:border-box; }
body {
  margin:0; background:#f3f6fa; color:var(--ink);
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
}
main { max-width:1440px; margin:0 auto; padding:28px; }
h1 { margin:0 0 4px; font-size:26px; } h2 { margin:26px 0 10px; font-size:18px; }
.subtitle { color:var(--muted); margin-bottom:18px; }
.metrics { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; }
.metric { background:white; border:1px solid var(--line); border-radius:10px; padding:13px 15px; }
.metric span { display:block; color:var(--muted); font-size:12px; }
.metric strong { display:block; margin-top:4px; font-size:20px; }
.warning {
  margin:14px 0; padding:12px 14px; border:1px solid #f5c26b;
  border-radius:8px; background:#fff8e8; color:#8a4b08;
}
.table-wrap { overflow:auto; background:white; border:1px solid var(--line); border-radius:10px; }
.dataframe { width:100%%; border-collapse:collapse; white-space:nowrap; }
.dataframe th,.dataframe td { padding:8px 10px; border-bottom:1px solid var(--line); text-align:right; }
.dataframe th { position:sticky; top:0; background:var(--panel); color:#475467; }
.dataframe th:first-child,.dataframe td:first-child { text-align:left; }
.empty { color:var(--muted); padding:14px; background:white; border-radius:8px; }
.footnote { margin-top:22px; color:var(--muted); font-size:12px; }
</style>
</head>
<body><main>
<h1>QMT真实绩效报告</h1>
<div class="subtitle">截至 %s · 数据源仅为券商QMT事实，不读取影子账本</div>
%s
<section class="metrics">%s</section>
<h2>最近净值</h2><div class="table-wrap">%s</div>
<h2>最近订单与成交</h2><div class="table-wrap">%s</div>
<div class="footnote">外部资金流优先采用券商字段；未知费用保持missing，不使用模拟费率补齐。</div>
</main></body></html>
""" % (latest_date, missing_warning, cards, nav_table, trade_table)
    _atomic_write_text(report_path, document)
    return report_path


def update_performance(
    result: Optional[Payload] = None,
    eod_snapshot: Optional[Payload] = None,
    output_dir: Union[str, os.PathLike] = DEFAULT_PERFORMANCE_DIR,
    benchmark_returns: Optional[BenchmarkInput] = None,
    **aliases: Any,
) -> Dict[str, pd.DataFrame]:
    """Import one QMT execution day and safely update all three ledgers.

    ``result`` and ``eod_snapshot`` may be dictionaries or JSON paths.  For
    compatibility with callers that prefer explicit names, ``result_path``,
    ``eod_snapshot_path`` and ``performance_dir`` are accepted as aliases.
    Re-importing the same batch replaces that batch's trade rows and the same
    date's NAV row, so it never duplicates fills or returns.
    """

    if result is None:
        result = aliases.pop("result_path", None)
    if eod_snapshot is None:
        eod_snapshot = aliases.pop("eod_snapshot_path", None)
    if "performance_dir" in aliases:
        output_dir = aliases.pop("performance_dir")
    if aliases:
        raise TypeError("unexpected arguments: %s" % ", ".join(sorted(aliases)))

    result_payload = _load_payload(result, "result")
    eod_payload = _load_payload(eod_snapshot, "eod_snapshot")
    if result_payload is None and eod_payload is None:
        raise ValueError("at least result.json or eod_snapshot.json is required")
    _validate_payload_identity(result_payload, eod_payload)

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    trade_path = directory / "qmt_trades.csv"
    nav_path = directory / "qmt_nav.csv"
    cash_flow_path = directory / "qmt_cash_flows.csv"

    benchmark_mapping = _benchmark_mapping(benchmark_returns)
    with _performance_lock(directory):
        # Validate the user-editable cash-flow ledger before mutating trades;
        # malformed flow data must never leave a half-updated batch behind.
        _normalise_cash_flows(_read_csv(cash_flow_path, CASH_FLOW_COLUMNS))
        _validate_partial_batch_replacement(
            result_payload,
            eod_payload,
            nav_path,
            trade_path,
        )
        trades = _update_trades(result_payload, trade_path)
        nav, cash_flows = _update_nav(
            result_payload,
            eod_payload,
            nav_path,
            cash_flow_path,
            trades,
            benchmark_mapping,
        )
        generate_report(nav, trades, directory / "qmt_report.html")
        return {"nav": nav, "trades": trades, "cash_flows": cash_flows}


# A readable alias for batch jobs and tests.
update_ledgers = update_performance


class QMTPerformanceLedger:
    """Small stateful wrapper around :func:`update_performance`."""

    def __init__(self, output_dir: Union[str, os.PathLike] = DEFAULT_PERFORMANCE_DIR):
        self.output_dir = Path(output_dir)

    def ingest(
        self,
        result: Optional[Payload] = None,
        eod_snapshot: Optional[Payload] = None,
        benchmark_returns: Optional[BenchmarkInput] = None,
    ) -> Dict[str, pd.DataFrame]:
        return update_performance(result, eod_snapshot, self.output_dir, benchmark_returns)

    def register_cash_flow(
        self, exec_date: str, amount: float, *, flow_id: Optional[str] = None, note: str = ""
    ) -> pd.DataFrame:
        return register_cash_flow(exec_date, amount, self.output_dir, flow_id=flow_id, note=note)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import QMT broker results into real-performance ledgers")
    parser.add_argument("--result", help="path to result.json")
    parser.add_argument("--eod-snapshot", help="path to eod_snapshot.json")
    parser.add_argument("--exec-date", help="use my/runtime/qmt_outbox/<date> default paths")
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--output-dir", default=str(DEFAULT_PERFORMANCE_DIR))
    parser.add_argument("--benchmark", help="CSV/JSON containing date and daily return")
    parser.add_argument("--cash-flow", type=float, help="manually register today's net external flow")
    parser.add_argument("--cash-flow-id")
    parser.add_argument("--cash-flow-note", default="")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    result_path = args.result
    eod_path = args.eod_snapshot
    if args.exec_date:
        date = _normalise_date(args.exec_date)
        day_dir = Path(args.runtime_root) / "qmt_outbox" / date
        result_path = result_path or str(day_dir / "result.json")
        eod_path = eod_path or str(day_dir / "eod_snapshot.json")
    if args.cash_flow is not None:
        if not args.exec_date:
            raise SystemExit("--cash-flow requires --exec-date")
        register_cash_flow(
            args.exec_date,
            args.cash_flow,
            args.output_dir,
            flow_id=args.cash_flow_id,
            note=args.cash_flow_note,
        )
    if not result_path and not eod_path:
        raise SystemExit("provide --result/--eod-snapshot or --exec-date")

    ledgers = update_performance(
        result=result_path,
        eod_snapshot=eod_path,
        output_dir=args.output_dir,
        benchmark_returns=args.benchmark,
    )
    summary = {
        "output_dir": str(Path(args.output_dir).resolve()),
        "nav_rows": len(ledgers["nav"]),
        "trade_rows": len(ledgers["trades"]),
        "cash_flow_rows": len(ledgers["cash_flows"]),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

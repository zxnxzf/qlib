"""Broker-independent QMT execution state machine and embedded-runtime entry points.

The state machine is complete and testable with a fake broker.  The embedded
QMT entry point defaults to read-only preflight.  Actual order submission is
loaded through a broker bridge only after ``qmt_probe.py`` has confirmed the
Guojin QMT API names, fields, status codes, and cancel signature.
"""

from __future__ import annotations

import importlib
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from my.quant.trade_planner import (
    AccountSnapshot,
    HoldingSnapshot,
    MarketSnapshot,
    QuoteSnapshot,
    SignalCandidate,
    SignalPackage,
    plan_buys,
    plan_sells,
)

from . import PLANNER_VERSION, RESULT_SCHEMA_VERSION
from .protocol import (
    QmtProtocolError,
    _write_lock,
    eod_snapshot_path,
    read_eod_snapshot,
    read_json,
    read_result,
    read_signal,
    result_path,
    signal_path,
    validate_signal,
    write_eod_snapshot,
    write_result,
)


SHANGHAI_TZ = timezone(timedelta(hours=8))
TERMINAL_PHASES = {"completed", "partial", "aborted"}
RECOVERY_PHASES = {"sell_submitted", "sell_closed", "buy_submitted"}
ALLOWED_BRIDGE = ("my.qmt.guojin_bridge", "GuojinQmtBridge")


class QmtExecutionError(RuntimeError):
    pass


class RecoveryRequiredError(QmtExecutionError):
    pass


class ExecutionAlreadyRunningError(QmtExecutionError):
    pass


class BrokerAdapter(Protocol):
    """Narrow boundary implemented by the exact Guojin API bridge."""

    def account_snapshot(self) -> dict: ...

    def market_snapshot(self, codes: Sequence[str], exec_date: str) -> dict: ...

    def execute_stage(self, stage: str, orders: Sequence[dict], wait_seconds: int) -> dict: ...

    def recover_stage(self, stage: str, orders: Sequence[dict], wait_seconds: int) -> Optional[dict]: ...


def _now_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def normalize_code(value: object) -> str:
    text = str(value or "").strip().upper()
    if text.startswith(("SH", "SZ", "BJ")) and len(text) == 8 and text[2:].isdigit():
        return text
    if len(text) == 9 and text[6] == "." and text[:6].isdigit():
        exchange = text[7:]
        if exchange in {"SH", "SZ", "BJ"}:
            return exchange + text[:6]
    return text


def qmt_code(value: object) -> str:
    code = normalize_code(value)
    if code.startswith(("SH", "SZ", "BJ")) and len(code) == 8:
        return code[2:] + "." + code[:2]
    return code


def _finite_number(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _atomic_replace_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(rendered)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))


def _state_path(runtime_root: Path, exec_date: str, batch_id: str) -> Path:
    safe_batch = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in batch_id)
    return Path(runtime_root) / "qmt_state" / exec_date / (safe_batch + ".json")


def _load_state(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise QmtExecutionError("QMT state must be a JSON object")
    return payload


def _account_for_planner(payload: dict) -> AccountSnapshot:
    if not isinstance(payload, dict):
        raise QmtExecutionError("broker account snapshot must be an object")
    cash = _finite_number(payload.get("cash"), default=-1.0)
    if cash < 0:
        raise QmtExecutionError("broker account snapshot has invalid cash")
    holdings = {}
    raw_holdings = payload.get("holdings", [])
    if not isinstance(raw_holdings, list):
        raise QmtExecutionError("broker holdings must be a list")
    for row in raw_holdings:
        if not isinstance(row, dict):
            raise QmtExecutionError("broker holding must be an object")
        code = normalize_code(row.get("code"))
        shares = int(_finite_number(row.get("shares")))
        available = int(_finite_number(row.get("available_shares"), shares))
        held_days = int(_finite_number(row.get("held_days"), 1))
        if not code or shares < 0 or available < 0 or available > shares:
            raise QmtExecutionError("broker holding contains invalid code or shares")
        if shares:
            holdings[code] = HoldingSnapshot(shares, available, held_days)
    return AccountSnapshot(cash=cash, holdings=holdings)


def _market_for_planner(payload: dict, exec_date: str) -> MarketSnapshot:
    if not isinstance(payload, dict):
        raise QmtExecutionError("broker market snapshot must be an object")
    quotes = {}
    for raw_code, row in payload.items():
        if not isinstance(row, dict):
            raise QmtExecutionError("broker quote must be an object")
        code = normalize_code(raw_code)
        quotes[code] = QuoteSnapshot(
            code=code,
            timestamp=str(row.get("timestamp", "")),
            bid1=_finite_number(row.get("bid1")),
            ask1=_finite_number(row.get("ask1")),
            last=_finite_number(row.get("last")),
            high_limit=_finite_number(row.get("high_limit")),
            low_limit=_finite_number(row.get("low_limit")),
            buyable=bool(row.get("buyable", False)),
            sellable=bool(row.get("sellable", False)),
            status=str(row.get("status", "unknown")),
            risk_blocked=bool(row.get("risk_blocked", False)),
            risk_reason=str(row.get("risk_reason", "")),
        )
    return MarketSnapshot(exec_date=exec_date, quotes=quotes)


def package_from_signal(signal: dict, holding_codes: Iterable[str]) -> SignalPackage:
    scores = signal["scores"]
    candidates = tuple(
        SignalCandidate(
            code=normalize_code(row["code"]),
            score=float(row["score"]),
            rank=int(row["rank"]),
            reference_close=float(row["reference_close"]),
        )
        for row in signal["candidates"]
    )
    return SignalPackage(
        batch_id=str(signal["batch_id"]),
        signal_date=str(signal["signal_date"]),
        exec_date=str(signal["exec_date"]),
        gate_on=bool(signal["gate"]["on"]),
        candidates=candidates,
        holding_scores={
            normalize_code(code): (
                None if scores.get(normalize_code(code)) is None else float(scores[normalize_code(code)])
            )
            for code in holding_codes
        },
        params=dict(signal["params"]),
        provenance=dict(signal["provenance"]),
    )


def _planned_order(order, order_id: str) -> dict:
    submit_price = order.price_ceiling if order.side == "buy" else order.price_floor
    return {
        "order_id": order_id,
        "code": order.code,
        "qmt_code": qmt_code(order.code),
        "side": order.side,
        "shares": int(order.shares),
        "reference_price": float(order.limit_price),
        "submit_price": float(submit_price),
        "price_floor": float(order.price_floor),
        "price_ceiling": float(order.price_ceiling),
        "reason": order.reason,
        "candidate_rank": order.candidate_rank,
    }


def _skip_payload(skip) -> dict:
    return {"code": skip.code, "side": skip.side, "reason": skip.reason}


def _empty_outcome() -> dict:
    return {"terminal": True, "broker_orders": [], "fills": [], "cancelled": [], "errors": []}


def _fact_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise QmtExecutionError("%s must be a non-negative integer" % field_name)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise QmtExecutionError("%s must be a non-negative integer" % field_name) from exc
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        raise QmtExecutionError("%s must be a non-negative integer" % field_name)
    return int(number)


def _validate_outcome_facts(stage: str, planned: Sequence[dict], outcome: dict) -> None:
    planned_by_id: Dict[str, dict] = {}
    for row in planned:
        order_id = str(row.get("order_id", "")).strip() if isinstance(row, dict) else ""
        if not order_id or order_id in planned_by_id:
            raise QmtExecutionError("planned QMT order ids must be non-empty and unique")
        planned_by_id[order_id] = row

    filled_by_order: Dict[str, int] = {}
    fill_ids = set()
    for collection_name in ("broker_orders", "fills", "cancelled"):
        for index, fact in enumerate(outcome[collection_name]):
            if not isinstance(fact, dict):
                raise QmtExecutionError("broker %s fact %d must be an object" % (collection_name, index))
            order_id = str(fact.get("order_id", "")).strip()
            if order_id not in planned_by_id:
                raise QmtExecutionError("broker %s fact references an unknown order_id" % collection_name)
            planned_row = planned_by_id[order_id]
            if fact.get("code") not in (None, "") and normalize_code(fact["code"]) != planned_row["code"]:
                raise QmtExecutionError("broker %s fact code does not match its plan" % collection_name)
            if fact.get("side") not in (None, "") and str(fact["side"]).lower() != stage:
                raise QmtExecutionError("broker %s fact side does not match its stage" % collection_name)

            if collection_name == "fills":
                fill_id = str(fact.get("fill_id", "")).strip()
                if not fill_id or fill_id in fill_ids:
                    raise QmtExecutionError("broker fill ids must be non-empty and unique")
                fill_ids.add(fill_id)
                shares = _fact_integer(fact.get("shares"), "broker fill shares")
                price = _finite_number(fact.get("price"), default=-1.0)
                if shares > 0 and price <= 0:
                    raise QmtExecutionError("broker fill price must be positive when shares are filled")
                filled_by_order[order_id] = filled_by_order.get(order_id, 0) + shares
            elif fact.get("shares") is not None:
                _fact_integer(fact.get("shares"), "broker %s shares" % collection_name)

    for order_id, filled in filled_by_order.items():
        if filled > int(planned_by_id[order_id]["shares"]):
            raise QmtExecutionError("broker fills exceed planned shares for %s" % order_id)


def _normalize_outcome(
    outcome: Optional[dict], stage: str, planned: Sequence[dict]
) -> Optional[dict]:
    if outcome is None:
        return None
    if not isinstance(outcome, dict):
        raise QmtExecutionError("broker stage outcome must be an object")
    terminal = outcome.get("terminal")
    if not isinstance(terminal, bool):
        raise QmtExecutionError("broker stage outcome.terminal must be explicit boolean proof")
    normalized = {"terminal": terminal}
    for key in ("broker_orders", "fills", "cancelled", "errors"):
        value = outcome.get(key, [])
        if not isinstance(value, list):
            raise QmtExecutionError("broker stage outcome.%s must be a list" % key)
        normalized[key] = value
    _validate_outcome_facts(stage, planned, normalized)
    return normalized


def _require_terminal_outcome(stage: str, outcome: dict) -> None:
    if not outcome.get("terminal", False):
        raise RecoveryRequiredError(
            "%s broker orders are not proven terminal; keep submitted state and retry recovery" % stage
        )


def _stage_complete(planned: Sequence[dict], outcome: dict) -> bool:
    filled: Dict[str, int] = {}
    for row in outcome["fills"]:
        if isinstance(row, dict):
            order_id = str(row.get("order_id", ""))
            filled[order_id] = filled.get(order_id, 0) + int(_finite_number(row.get("shares")))
    return not outcome["errors"] and all(filled.get(row["order_id"], 0) >= int(row["shares"]) for row in planned)


@dataclass
class QmtExecutionEngine:
    runtime_root: Path
    account_alias: str
    broker: BrokerAdapter
    now_fn: object = _now_shanghai
    lock_timeout_seconds: float = 0.25

    def _now(self) -> datetime:
        value = self.now_fn()
        if value.tzinfo is None:
            value = value.replace(tzinfo=SHANGHAI_TZ)
        return value.astimezone(SHANGHAI_TZ)

    def _finish(self, state: dict, status: str, reason: Optional[str]) -> dict:
        account_after = self.broker.account_snapshot()
        result = state["result"]
        result["account_after"] = account_after
        result["finished_at"] = self._now().isoformat()
        result["status"] = status
        result["reason"] = reason
        write_result(self.runtime_root, result)
        state["phase"] = status
        state["result"] = result
        state["updated_at"] = result["finished_at"]
        _atomic_replace_json(Path(state["state_path"]), state)
        return read_result(
            self.runtime_root,
            result["exec_date"],
            expected_batch_id=result["batch_id"],
            expected_planner_version=PLANNER_VERSION,
        )

    def _abort_recovery(self, state: dict, stage: str) -> dict:
        message = "%s recovery could not prove broker terminal state; manual review required" % stage
        state["result"]["errors"].append({"stage": stage, "reason": message})
        return self._finish(state, "aborted", "manual_reconciliation_required")

    def run(self, exec_date: str, signal: Optional[dict] = None) -> dict:
        """Run one execution date under a cross-process, date-wide lock."""
        try:
            parsed_exec_date = datetime.strptime(str(exec_date), "%Y-%m-%d").date().isoformat()
        except (TypeError, ValueError) as exc:
            raise QmtExecutionError("exec_date must be a valid YYYY-MM-DD date") from exc
        if parsed_exec_date != exec_date:
            raise QmtExecutionError("exec_date must use YYYY-MM-DD format")
        lock_target = Path(self.runtime_root) / "qmt_state" / exec_date / "execution"
        lock_target.parent.mkdir(parents=True, exist_ok=True)
        lock_context = _write_lock(lock_target, timeout_seconds=self.lock_timeout_seconds)
        try:
            lock_context.__enter__()
        except QmtProtocolError as exc:
            if "timed out waiting" in str(exc):
                raise ExecutionAlreadyRunningError(
                    "another QMT execution instance is already running for %s" % exec_date
                ) from exc
            raise QmtExecutionError("cannot acquire QMT execution lock: %s" % exc) from exc
        try:
            return self._run_locked(exec_date, signal)
        finally:
            lock_context.__exit__(None, None, None)

    def _run_locked(self, exec_date: str, signal: Optional[dict] = None) -> dict:
        if signal is not None:
            signal = validate_signal(
                signal,
                expected_exec_date=exec_date,
                expected_account_alias=self.account_alias,
                expected_planner_version=PLANNER_VERSION,
                check_expiry=False,
            )
        existing_result = result_path(self.runtime_root, exec_date)
        if existing_result.exists():
            return read_result(
                self.runtime_root,
                exec_date,
                expected_batch_id=(str(signal["batch_id"]) if signal is not None else None),
                expected_planner_version=PLANNER_VERSION,
            )
        if signal is None:
            recovery_signal = validate_signal(
                read_json(signal_path(self.runtime_root, exec_date)),
                expected_exec_date=exec_date,
                expected_account_alias=self.account_alias,
                expected_planner_version=PLANNER_VERSION,
                check_expiry=False,
            )
            batch_id = str(recovery_signal["batch_id"])
            state_file = _state_path(self.runtime_root, exec_date, batch_id)
            state = _load_state(state_file)
            if state is not None and state.get("phase") in RECOVERY_PHASES:
                signal = recovery_signal
            else:
                signal = read_signal(
                    self.runtime_root,
                    exec_date,
                    expected_account_alias=self.account_alias,
                    expected_planner_version=PLANNER_VERSION,
                    now=self._now(),
                )
        else:
            batch_id = str(signal["batch_id"])
            state_file = _state_path(self.runtime_root, exec_date, batch_id)
            state = _load_state(state_file)
            if state is None or state.get("phase") not in RECOVERY_PHASES:
                signal = validate_signal(
                    signal,
                    expected_exec_date=exec_date,
                    expected_account_alias=self.account_alias,
                    expected_planner_version=PLANNER_VERSION,
                    now=self._now(),
                    check_expiry=True,
                )

        if state is None:
            account_before = self.broker.account_snapshot()
            planner_account = _account_for_planner(account_before)
            required_codes = list(planner_account.holdings) + [row["code"] for row in signal["candidates"]]
            market_payload = self.broker.market_snapshot(sorted(set(required_codes)), exec_date)
            market = _market_for_planner(market_payload, exec_date)
            package = package_from_signal(signal, planner_account.holdings)
            sell_plan = plan_sells(package, planner_account, market)
            sell_orders = [
                _planned_order(order, "%s:sell:%03d" % (batch_id, index))
                for index, order in enumerate(sell_plan.orders, start=1)
            ]
            started_at = self._now().isoformat()
            result = {
                "schema_version": RESULT_SCHEMA_VERSION,
                "batch_id": batch_id,
                "signal_date": signal["signal_date"],
                "exec_date": exec_date,
                "planner_version": PLANNER_VERSION,
                "started_at": started_at,
                "finished_at": started_at,
                "status": "aborted",
                "reason": "in_progress",
                "account_before": account_before,
                "market_snapshot": market_payload,
                "buy_market_snapshot": {},
                "sell_stage": {
                    "planned": sell_orders,
                    "skipped": [_skip_payload(skip) for skip in sell_plan.skips],
                    **_empty_outcome(),
                },
                "account_after_sell": {},
                "buy_stage": {"planned": [], "skipped": [], **_empty_outcome()},
                "account_after": {},
                "errors": [],
            }
            state = {
                "schema_version": 1,
                "batch_id": batch_id,
                "exec_date": exec_date,
                "phase": "ready",
                "state_path": str(state_file),
                "result": result,
                "sell_complete": True,
                "buy_complete": True,
                "updated_at": started_at,
            }
            _atomic_replace_json(state_file, state)

        if state.get("batch_id") != batch_id or state.get("exec_date") != exec_date:
            raise QmtExecutionError("QMT state batch/date mismatch")
        if state.get("phase") in TERMINAL_PHASES:
            raise QmtExecutionError("terminal state exists without immutable result")

        phase = state["phase"]
        sell_orders = state["result"]["sell_stage"]["planned"]
        if phase == "ready":
            state["result"]["sell_stage"]["terminal"] = False
            state["phase"] = "sell_submitted"
            state["updated_at"] = self._now().isoformat()
            _atomic_replace_json(state_file, state)
            outcome = (
                _normalize_outcome(
                    self.broker.execute_stage("sell", sell_orders, int(signal["params"]["wait_seconds"])),
                    "sell",
                    sell_orders,
                )
                if sell_orders
                else _empty_outcome()
            )
            _require_terminal_outcome("sell", outcome)
        elif phase == "sell_submitted":
            outcome = _normalize_outcome(
                self.broker.recover_stage("sell", sell_orders, int(signal["params"]["wait_seconds"])),
                "sell",
                sell_orders,
            )
            if outcome is None:
                return self._abort_recovery(state, "sell")
            _require_terminal_outcome("sell", outcome)
        else:
            outcome = None

        if phase in {"ready", "sell_submitted"}:
            state["result"]["sell_stage"].update(outcome or _empty_outcome())
            state["sell_complete"] = _stage_complete(sell_orders, outcome or _empty_outcome())
            state["result"]["errors"].extend((outcome or _empty_outcome())["errors"])
            account_after_sell = self.broker.account_snapshot()
            state["result"]["account_after_sell"] = account_after_sell
            state["phase"] = "sell_closed"
            state["updated_at"] = self._now().isoformat()
            _atomic_replace_json(state_file, state)
            phase = "sell_closed"

        if phase == "sell_closed":
            account_after_sell = state["result"]["account_after_sell"]
            planner_account = _account_for_planner(account_after_sell)
            required_codes = list(planner_account.holdings) + [row["code"] for row in signal["candidates"]]
            refreshed_market = self.broker.market_snapshot(sorted(set(required_codes)), exec_date)
            state["result"]["buy_market_snapshot"] = refreshed_market
            package = package_from_signal(signal, planner_account.holdings)
            buy_plan = plan_buys(package, planner_account, _market_for_planner(refreshed_market, exec_date))
            buy_orders = [
                _planned_order(order, "%s:buy:%03d" % (batch_id, index))
                for index, order in enumerate(buy_plan.orders, start=1)
            ]
            state["result"]["buy_stage"]["planned"] = buy_orders
            state["result"]["buy_stage"]["skipped"] = [_skip_payload(skip) for skip in buy_plan.skips]
            state["result"]["buy_stage"]["terminal"] = False
            state["phase"] = "buy_submitted"
            state["updated_at"] = self._now().isoformat()
            _atomic_replace_json(state_file, state)
            outcome = (
                _normalize_outcome(
                    self.broker.execute_stage("buy", buy_orders, int(signal["params"]["wait_seconds"])),
                    "buy",
                    buy_orders,
                )
                if buy_orders
                else _empty_outcome()
            )
            _require_terminal_outcome("buy", outcome)
        elif phase == "buy_submitted":
            buy_orders = state["result"]["buy_stage"]["planned"]
            outcome = _normalize_outcome(
                self.broker.recover_stage("buy", buy_orders, int(signal["params"]["wait_seconds"])),
                "buy",
                buy_orders,
            )
            if outcome is None:
                return self._abort_recovery(state, "buy")
            _require_terminal_outcome("buy", outcome)
        else:
            raise QmtExecutionError("cannot continue from QMT phase %s" % phase)

        state["result"]["buy_stage"].update(outcome or _empty_outcome())
        state["buy_complete"] = _stage_complete(
            state["result"]["buy_stage"]["planned"], outcome or _empty_outcome()
        )
        state["result"]["errors"].extend((outcome or _empty_outcome())["errors"])
        complete = bool(state["sell_complete"] and state["buy_complete"] and not state["result"]["errors"])
        return self._finish(
            state,
            "completed" if complete else "partial",
            None if complete else "one_or_more_orders_not_fully_filled",
        )


def read_only_preflight(signal: dict, broker: BrokerAdapter, runtime_root: Path, now=None) -> Path:
    """Query real account/quotes and plan sells without submitting any order."""
    account_payload = broker.account_snapshot()
    account = _account_for_planner(account_payload)
    codes = list(account.holdings) + [row["code"] for row in signal["candidates"]]
    market_payload = broker.market_snapshot(sorted(set(codes)), signal["exec_date"])
    package = package_from_signal(signal, account.holdings)
    plan = plan_sells(package, account, _market_for_planner(market_payload, signal["exec_date"]))
    timestamp = now or _now_shanghai()
    payload = {
        "schema_version": 1,
        "batch_id": signal["batch_id"],
        "exec_date": signal["exec_date"],
        "created_at": timestamp.isoformat(),
        "mode": "read_only",
        "account": account_payload,
        "market_snapshot": market_payload,
        "sell_plan": [
            _planned_order(order, "%s:sell:%03d" % (signal["batch_id"], index))
            for index, order in enumerate(plan.orders, 1)
        ],
        "sell_skips": [_skip_payload(skip) for skip in plan.skips],
    }
    path = Path(runtime_root) / "qmt_state" / signal["exec_date"] / "read_only_preflight.json"
    _atomic_replace_json(path, payload)
    return path


def write_eod_from_broker(
    runtime_root: Path,
    exec_date: str,
    batch_id: str,
    broker: BrokerAdapter,
    now: Optional[datetime] = None,
    external_cash_flow: Optional[float] = None,
) -> dict:
    """Persist the broker's closing account fact; never synthesize total asset."""
    timestamp = now or _now_shanghai()
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=SHANGHAI_TZ)
    timestamp = timestamp.astimezone(SHANGHAI_TZ)
    if timestamp.time().replace(tzinfo=None) < time(15, 0):
        raise QmtExecutionError("EOD snapshot is forbidden before 15:00 Asia/Shanghai")
    if eod_snapshot_path(runtime_root, exec_date).exists():
        return read_eod_snapshot(runtime_root, exec_date, expected_batch_id=batch_id)
    account = broker.account_snapshot()
    if not isinstance(account, dict) or account.get("source") != "broker_qmt":
        raise QmtExecutionError("EOD account snapshot must be an explicit broker_qmt fact")

    def required_nonnegative(field: str) -> float:
        value = account.get(field)
        if isinstance(value, bool):
            raise QmtExecutionError("EOD broker %s must be a non-negative finite number" % field)
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise QmtExecutionError(
                "EOD broker %s must be a non-negative finite number" % field
            ) from exc
        if not math.isfinite(number) or number < 0:
            raise QmtExecutionError("EOD broker %s must be a non-negative finite number" % field)
        return number

    holdings = account.get("holdings")
    if not isinstance(holdings, list):
        raise QmtExecutionError("EOD broker holdings must be a list")
    total_asset = account.get("total_asset")
    if total_asset is not None:
        total_asset = required_nonnegative("total_asset")
    payload = {
        "schema_version": 1,
        "batch_id": batch_id,
        "exec_date": exec_date,
        "snapshot_at": timestamp.isoformat(),
        "cash": required_nonnegative("cash"),
        "frozen_cash": required_nonnegative("frozen_cash"),
        "market_value": required_nonnegative("market_value"),
        "total_asset": total_asset,
        "holdings": list(holdings),
        "external_cash_flow": external_cash_flow,
        "source": "broker_qmt",
    }
    write_eod_snapshot(runtime_root, payload)
    return read_eod_snapshot(runtime_root, exec_date, expected_batch_id=batch_id)


def load_local_config(path: Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"repo_root", "account_alias", "account_id", "account_type", "mode"}
    missing = sorted(required.difference(payload))
    if missing:
        raise QmtExecutionError("QMT local config missing: %s" % ", ".join(missing))
    if payload["mode"] != "read_only":
        raise QmtExecutionError("first release permits read_only mode only; simulation remains locked")
    return payload


QMT_API_NAMESPACE = None


def load_broker_bridge(config: dict, context_info) -> BrokerAdapter:
    module_name = str(config.get("bridge_module") or "")
    class_name = str(config.get("bridge_class") or "")
    if (module_name, class_name) != ALLOWED_BRIDGE:
        raise QmtExecutionError(
            "first release only permits the reviewed read-only GuojinQmtBridge"
        )
    bridge_class = getattr(importlib.import_module(module_name), class_name)
    return bridge_class(
        context_info=context_info,
        config=config,
        api_namespace=QMT_API_NAMESPACE,
    )


# QMT users may set this absolute path after copying the strategy script.
QMT_CONFIG_PATH = os.environ.get("QLIB_QMT_CONFIG", "")


def init(ContextInfo):
    if not QMT_CONFIG_PATH:
        print("[QMT] QLIB_QMT_CONFIG/QMT_CONFIG_PATH is not configured; no trading action will run")
        ContextInfo._qlib_qmt_config = None
        return
    config = load_local_config(Path(QMT_CONFIG_PATH))
    ContextInfo._qlib_qmt_config = config
    ContextInfo.accid = config["account_id"]
    ContextInfo._qlib_qmt_processed = set()
    print("[QMT] initialized in %s mode for alias=%s" % (config["mode"], config["account_alias"]))


def handlebar(ContextInfo):
    is_last_bar = getattr(ContextInfo, "is_last_bar", None)
    if not callable(is_last_bar) or not bool(is_last_bar()):
        return
    config = getattr(ContextInfo, "_qlib_qmt_config", None)
    if not config:
        return
    now = _now_shanghai()
    exec_date = now.date().isoformat()
    processed = getattr(ContextInfo, "_qlib_qmt_processed", set())
    runtime_root = Path(config["repo_root"]) / "my" / "runtime"
    broker = None
    trade_key = "trade:" + exec_date
    if time(9, 30) <= now.time().replace(tzinfo=None) < time(9, 32) and trade_key not in processed:
        signal = read_signal(
            runtime_root,
            exec_date,
            expected_account_alias=config["account_alias"],
            expected_planner_version=PLANNER_VERSION,
            now=now,
        )
        broker = load_broker_bridge(config, ContextInfo)
        if config["mode"] != "read_only":
            raise QmtExecutionError("execution mode is locked in the first release")
        path = read_only_preflight(signal, broker, runtime_root, now=now)
        print("[QMT] read-only preflight written: %s" % path)
        processed.add(trade_key)

    eod_key = "eod:" + exec_date
    if now.time().replace(tzinfo=None) >= time(15, 0) and eod_key not in processed:
        if result_path(runtime_root, exec_date).exists():
            batch_id = read_result(runtime_root, exec_date)["batch_id"]
        else:
            raw_signal = validate_signal(
                read_json(signal_path(runtime_root, exec_date)),
                expected_exec_date=exec_date,
                expected_account_alias=config["account_alias"],
                expected_planner_version=PLANNER_VERSION,
                check_expiry=False,
            )
            batch_id = raw_signal["batch_id"]
        broker = broker or load_broker_bridge(config, ContextInfo)
        snapshot = write_eod_from_broker(runtime_root, exec_date, batch_id, broker, now=now)
        print("[QMT] EOD snapshot written; total_asset=%s" % snapshot.get("total_asset"))
        processed.add(eod_key)
    ContextInfo._qlib_qmt_processed = processed


__all__ = [
    "BrokerAdapter",
    "QmtExecutionEngine",
    "QmtExecutionError",
    "RecoveryRequiredError",
    "ExecutionAlreadyRunningError",
    "normalize_code",
    "qmt_code",
    "package_from_signal",
    "read_only_preflight",
    "write_eod_from_broker",
    "load_local_config",
    "load_broker_bridge",
    "init",
    "handlebar",
]

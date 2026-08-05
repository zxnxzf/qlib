"""影子模式两阶段编排：T-1 固定信号，T 日先卖后买。"""

from typing import Dict, Optional, Tuple

import pandas as pd

from . import config as C
from . import data, gate, ledger, portfolio, signal_
from .execution import ExecutionAdapter, Receipt, ShadowExecutor
from .signal_package import build_signal_package, load_signal_package, save_signal_package
from .trade_planner import (
    AccountSnapshot,
    HoldingSnapshot,
    MarketSnapshot,
    QuoteSnapshot,
    apply_receipts,
    plan_buys,
    plan_sells,
)


def _merge_retry_sells(orders, retry_sells):
    """把受阻卖单放到买单前重试，并按股票去重，防止同一持仓被卖两次。"""
    existing_sell_codes = {o.code for o in orders if o.side == "sell"}
    retries = []
    for od in retry_sells:
        if od["code"] in existing_sell_codes:
            continue
        retries.append(
            portfolio.Order(
                od["code"], od["side"], od["shares"], od["ref_price"], od["reason"] + "_retry"
            )
        )
        existing_sell_codes.add(od["code"])
    return retries + orders


def _mark_to_market(
    holdings: Dict[str, int], cash: float, date: str, last_prices: Dict[str, float]
) -> Tuple[float, Dict[str, float]]:
    prices = dict(last_prices)
    if not holdings:
        return cash, prices
    bars = data.day_bars(date, fields=("$close", "$factor"))
    total = cash
    for code, sh in holdings.items():
        if sh <= 0:
            continue
        if code in bars.index:
            row = bars.loc[code]
            close_adj, factor = row["close"], row["factor"]
            if close_adj == close_adj:
                raw = float(close_adj) / float(factor) if factor and factor == factor and factor > 0 else float(close_adj)
                total += sh * raw
                prices[code] = raw
                continue
        total += sh * prices.get(code, 0.0)  # 停牌沿用持久化的最近价
    return total, prices


def _ensure_fresh(asof: Optional[str], skip_update: bool, log) -> Tuple[str, Optional[dict]]:
    if not skip_update:
        ok = data.update_data()
        log(f"[nightly] 数据更新 {'成功' if ok else '失败（沿用本地数据）'}")
    today = asof or data.expected_signal_date()
    latest = data.latest_data_date()
    if latest < today:
        ledger.append_log(f"STALL {today} 数据未覆盖（本地最新 {latest}）")
        log(f"[nightly] 停摆：本地数据最新 {latest} < 应处理信号日 {today}")
        return today, {"date": today, "stall": True}
    return today, None


def _planner_params() -> dict:
    return {
        "topk": C.TOPK,
        "candidate_limit": 100,
        "n_drop": C.N_DROP,
        "hold_thresh": 1,
        "risk_degree": C.RISK_DEGREE,
        "lot": C.LOT,
        "open_cost": C.OPEN_COST,
        "close_cost": C.CLOSE_COST,
        "min_cost": C.MIN_COST,
        "max_slippage": 0.003,
    }


def _raw_closes(date: str) -> Dict[str, float]:
    bars = data.day_bars(date, fields=("$close", "$factor"))
    closes = {}
    for code, row in bars.iterrows():
        close, factor = row["close"], row["factor"]
        if close != close:
            continue
        raw = float(close) / float(factor) if factor and factor == factor and factor > 0 else float(close)
        if raw > 0:
            closes[str(code)] = raw
    return closes


def _market_snapshot(exec_date: str) -> MarketSnapshot:
    bars = data.day_bars(exec_date)
    quotes = {}
    for raw_code, row in bars.iterrows():
        code = str(raw_code)
        open_adj = row["open"]
        prev_close = row["prev_close"]
        volume = row["volume"]
        factor = row["factor"]
        tradable = (
            open_adj == open_adj
            and volume == volume
            and float(volume) > 0
            and factor == factor
            and float(factor) > 0
        )
        raw_open = float(open_adj) / float(factor) if tradable else 0.0
        raw_previous = (
            float(prev_close) / float(factor)
            if tradable and prev_close == prev_close and float(prev_close) > 0
            else raw_open
        )
        change = float(open_adj) / float(prev_close) - 1 if tradable and raw_previous > 0 else 0.0
        if not tradable:
            status = "suspended"
        elif change > C.LIMIT_TH or change < -C.LIMIT_TH:
            status = "blocked_limit"
        else:
            status = "normal"
        quotes[code] = QuoteSnapshot(
            code=code,
            timestamp=f"{exec_date}T09:30:00",
            bid1=raw_open,
            ask1=raw_open,
            last=raw_open,
            high_limit=raw_previous * 1.1,
            low_limit=raw_previous * 0.9,
            buyable=tradable and change <= C.LIMIT_TH,
            sellable=tradable and change >= -C.LIMIT_TH,
            status=status,
        )
    return MarketSnapshot(exec_date=exec_date, quotes=quotes)


def _account_snapshot(state: dict) -> AccountSnapshot:
    return AccountSnapshot(
        cash=float(state["cash"]),
        holdings={
            str(code): HoldingSnapshot(int(shares), int(shares), 1)
            for code, shares in state["holdings"].items()
            if int(shares) > 0
        },
    )


def _store_account(state: dict, account: AccountSnapshot) -> None:
    state["cash"] = float(account.cash)
    state["holdings"] = {
        code: int(holding.shares)
        for code, holding in account.holdings.items()
        if int(holding.shares) > 0
    }


def _orders_incomplete(orders, receipts: Tuple[Receipt, ...]) -> bool:
    filled = {}
    for receipt in receipts:
        key = (receipt.code, receipt.side)
        filled[key] = filled.get(key, 0) + int(receipt.shares)
    return any(filled.get((order.code, order.side), 0) < order.shares for order in orders)


def prepare(asof: Optional[str] = None, skip_update: bool = False, log=print) -> dict:
    """用 T-1 数据生成次交易日信号包，不计算最终买入股数。"""
    today, stalled = _ensure_fresh(asof, skip_update, log)
    if stalled:
        return stalled
    state = ledger.load_state()
    if state.get("pending_signal_date") == today and state.get("phase") == "signal_ready":
        return {"date": today, "prepared": state["pending_exec_date"], "noop": True}
    if state.get("phase") not in {"idle", "completed", "partial", "aborted"}:
        raise RuntimeError(
            f"上一批次尚未结束: {state.get('pending_batch_id')} phase={state.get('phase')}"
        )

    next_day = data.next_trade_date(today)
    if next_day is None:
        return {"date": today, "no_next_day": True}
    gate_on, note = gate.gate_for_next_day(today)
    log(f"[nightly] 门控: {note}")
    scores = signal_.scores_for(today, log=log) if gate_on else pd.Series(dtype=float)
    closes = _raw_closes(today) if gate_on else {}
    package = build_signal_package(
        scores=scores,
        signal_date=today,
        exec_date=next_day,
        gate_on=gate_on,
        holding_codes=state["holdings"],
        params=_planner_params(),
        batch_id=f"{today}_{next_day}",
        reference_closes=closes,
    )
    save_signal_package(package, C.STATE_DIR)
    state.update(
        {
            "pending_signal_date": today,
            "pending_exec_date": next_day,
            "pending_batch_id": package.batch_id,
            "phase": "signal_ready",
            "last_settled": today,
        }
    )
    ledger.save_state(state)
    return {
        "date": today,
        "gate_on": gate_on,
        "prepared": next_day,
        "candidates": len(package.candidates),
    }


def execute(
    exec_date: str,
    wait_seconds: int = 30,
    adapter: Optional[ExecutionAdapter] = None,
    market: Optional[MarketSnapshot] = None,
    log=print,
) -> dict:
    """执行已锁定信号包：卖单完成并落盘后，才规划买单。"""
    package = load_signal_package(exec_date, C.STATE_DIR)
    state = ledger.load_state()
    phase = state.get("phase", "idle")
    if state.get("pending_batch_id") != package.batch_id:
        raise ValueError(
            f"账本批次与信号包不一致: state={state.get('pending_batch_id')}, signal={package.batch_id}"
        )
    if phase in {"completed", "partial", "aborted"}:
        return {"date": exec_date, "phase": phase, "noop": True}
    if state.get("pending_exec_date") != exec_date:
        raise ValueError(
            f"账本执行日不一致: state={state.get('pending_exec_date')}, requested={exec_date}"
        )

    market = market or _market_snapshot(exec_date)
    if market.exec_date != exec_date:
        raise ValueError(f"行情执行日不一致: {market.exec_date} != {exec_date}")
    adapter = adapter or ShadowExecutor()
    sell_orders = ledger.load_planned_orders(exec_date, "sell")
    sell_receipts = ledger.load_receipts(exec_date, "sell")

    if phase == "signal_ready":
        account = _account_snapshot(state)
        sell_plan = plan_sells(package, account, market)
        sell_orders = list(sell_plan.orders)
        ledger.save_planned_orders(exec_date, "sell", sell_orders)
        ledger.save_plan_skips(exec_date, "sell", list(sell_plan.skips))
        state["phase"] = "sell_submitted"
        ledger.save_state(state)
        sell_receipts = (
            adapter.submit_and_wait(sell_orders, exec_date, account, market, wait_seconds)
            if sell_orders
            else []
        )
        ledger.save_receipts(exec_date, sell_receipts, stage="sell")
        account = apply_receipts(account, sell_receipts)
        _store_account(state, account)
        state["_sell_incomplete"] = _orders_incomplete(sell_orders, tuple(sell_receipts))
        state["phase"] = "sell_closed"
        ledger.save_state(state)
        phase = "sell_closed"
    elif phase == "sell_submitted":
        receipt_path = C.STATE_DIR / "receipts" / f"{exec_date}_sell.csv"
        if not receipt_path.exists():
            raise RuntimeError("卖单已提交但没有可恢复回执，禁止重复报单")
        account = apply_receipts(_account_snapshot(state), sell_receipts)
        _store_account(state, account)
        state["_sell_incomplete"] = _orders_incomplete(sell_orders, tuple(sell_receipts))
        state["phase"] = "sell_closed"
        ledger.save_state(state)
        phase = "sell_closed"

    if phase != "sell_closed":
        raise RuntimeError(f"无法从当前阶段继续执行: {phase}")

    account = _account_snapshot(state)
    buy_plan = plan_buys(package, account, market)
    buy_orders = list(buy_plan.orders)
    ledger.save_planned_orders(exec_date, "buy", buy_orders)
    ledger.save_plan_skips(exec_date, "buy", list(buy_plan.skips))
    state["phase"] = "buy_submitted"
    ledger.save_state(state)
    buy_receipts = (
        adapter.submit_and_wait(buy_orders, exec_date, account, market, wait_seconds)
        if buy_orders
        else []
    )
    ledger.save_receipts(exec_date, buy_receipts, stage="buy")
    account = apply_receipts(account, buy_receipts)
    _store_account(state, account)
    buy_incomplete = _orders_incomplete(buy_orders, tuple(buy_receipts))
    terminal = "partial" if state.pop("_sell_incomplete", False) or buy_incomplete else "completed"
    state.update(
        {
            "phase": terminal,
            "last_settled": exec_date,
        }
    )
    ledger.save_state(state)
    all_orders = sell_orders + buy_orders
    all_receipts = sell_receipts + buy_receipts
    fully_filled = sum(
        1
        for order in all_orders
        if sum(
            receipt.shares
            for receipt in all_receipts
            if receipt.code == order.code and receipt.side == order.side
        )
        >= order.shares
    )
    log(
        f"[nightly] {exec_date} 执行完成: phase={terminal} "
        f"卖{len(sell_orders)}/买{len(buy_orders)} 成交{fully_filled}/{len(all_orders)}"
    )
    return {
        "date": exec_date,
        "phase": terminal,
        "settled": f"{fully_filled}/{len(all_orders)}",
        "sell_orders": len(sell_orders),
        "buy_orders": len(buy_orders),
    }


def run_evening(asof: Optional[str] = None, skip_update: bool = False, log=print) -> dict:
    """历史回填兼容入口：执行今日信号包、记净值，再准备下一交易日。"""
    today, stalled = _ensure_fresh(asof, skip_update, log)
    if stalled:
        return stalled
    summary = {"date": today}

    state = ledger.load_state()
    if state.get("last_settled") and state["last_settled"] >= today:
        log(f"[nightly] {today} 不晚于已处理日期 {state['last_settled']}，幂等跳过")
        summary["noop"] = True
        return summary
    pending_date = state.get("pending_exec_date")
    if pending_date:
        if pending_date < today:
            ledger.append_log(f"ABORT {today} 过期信号包执行日 {pending_date}")
            return {"date": today, "stall": True, "reason": "stale_signal"}
        if pending_date == today:
            summary.update(execute(today, log=log))

    state = ledger.load_state()
    nav, last_prices = _mark_to_market(
        state["holdings"], state["cash"], today, state.get("last_prices", {})
    )
    state["last_prices"] = last_prices
    state["last_settled"] = today
    ledger.save_state(state)
    summary["nav"] = round(nav, 2)

    prepared = prepare(asof=today, skip_update=True, log=log)
    summary.update({key: value for key, value in prepared.items() if key != "date"})
    gate_on = bool(prepared.get("gate_on", False))
    ledger.append_nav(
        today,
        nav,
        state["cash"],
        len(state["holdings"]),
        gate_on,
        "no_next_day" if prepared.get("no_next_day") else "",
    )
    return summary

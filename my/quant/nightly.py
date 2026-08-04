"""每晚流程编排（影子模式一晚 = 一次 run）：

  1. 更新数据（可跳过，回填模式）并硬校验新鲜度
  2. 结算：昨晚挂出的订单按今日开盘价虚拟成交，更新持仓/现金
  3. 记净值（按今日收盘 mark-to-market）
  4. 门控判定（截至今日数据 → 明日状态）
  5. 打分并生成明日订单，挂单存档

停摆规则：数据未覆盖最新交易日 → 记停摆日志，不出新单（已挂订单顺延到数据恢复日结算）。
"""

from typing import Dict, Optional, Tuple

import pandas as pd

from . import config as C
from . import data, gate, ledger, portfolio, signal_
from .execution import ShadowExecutor


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


def run_evening(asof: Optional[str] = None, skip_update: bool = False, log=print) -> dict:
    """跑一个"晚上"。asof=None 时使用数据最新日期（先更新数据）。返回摘要 dict。"""
    if not skip_update:
        ok = data.update_data()
        log(f"[nightly] 数据更新 {'成功' if ok else '失败（沿用本地数据）'}")
    today = asof or data.expected_signal_date()
    summary = {"date": today}

    # --- 停摆检测：绝不把本地旧日误当作本次应处理的信号日 ---
    latest = data.latest_data_date()
    if latest < today:
        ledger.append_log(f"STALL {today} 数据未覆盖（本地最新 {latest}）")
        log(f"[nightly] 停摆：本地数据最新 {latest} < 应处理信号日 {today}")
        summary["stall"] = True
        return summary

    state = ledger.load_state()
    if state.get("last_settled") and state["last_settled"] >= today:
        log(f"[nightly] {today} 不晚于已处理日期 {state['last_settled']}，幂等跳过")
        summary["noop"] = True
        return summary

    # --- 1. 结算昨日挂单（其执行日=today） ---
    pending_date = state.get("pending_exec_date")
    if pending_date and pending_date <= today:
        orders = ledger.load_orders(pending_date)
        receipts = ShadowExecutor().settle(
            orders, pending_date, cash=state["cash"], holdings=state["holdings"]
        )
        ledger.save_receipts(pending_date, receipts)
        holdings, cash = state["holdings"], state["cash"]
        retry = []
        for o, r in zip(orders, receipts):
            if r.status == "filled":
                if r.side == "buy":
                    spend = r.shares * r.price + r.cost
                    if spend <= cash:
                        holdings[r.code] = holdings.get(r.code, 0) + r.shares
                        cash -= spend
                else:
                    holdings[r.code] = holdings.get(r.code, 0) - r.shares
                    if holdings[r.code] <= 0:
                        holdings.pop(r.code, None)
                    cash += r.shares * r.price - r.cost
            elif r.side == "sell" and r.status in ("blocked_limit", "suspended"):
                retry.append(o)  # 卖不掉的次日重试
        state["holdings"], state["cash"] = holdings, cash
        filled = sum(1 for r in receipts if r.status == "filled")
        log(f"[nightly] 结算 {pending_date}: {filled}/{len(receipts)} 成交，卖出待重试 {len(retry)}")
        summary["settled"] = f"{filled}/{len(receipts)}"
        state["_retry_sells"] = [o.__dict__ for o in retry]
        state["pending_exec_date"] = None

    # --- 2. 净值 ---
    nav, last_prices = _mark_to_market(
        state["holdings"], state["cash"], today, state.get("last_prices", {})
    )
    state["last_prices"] = last_prices
    summary["nav"] = round(nav, 2)

    # --- 3. 门控（明日状态） ---
    gate_on, note = gate.gate_for_next_day(today)
    summary["gate_on"] = gate_on
    log(f"[nightly] 门控: {note}")

    # --- 4. 生成明日订单 ---
    next_day = data.next_trade_date(today)
    if next_day is None:
        log("[nightly] 无法确定下一交易日，跳过出单")
        ledger.append_nav(today, nav, state["cash"], len(state["holdings"]), gate_on, "no_next_day")
        ledger.save_state(state)
        return summary

    bars = data.day_bars(today, fields=("$close", "$factor"))
    raw_close = {}
    for code, row in bars.iterrows():
        c, f = row["close"], row["factor"]
        if c == c:
            raw_close[code] = float(c) / float(f) if f and f == f and f > 0 else float(c)

    if gate_on:
        scores = signal_.scores_for(today, log=log)
        orders = portfolio.decide(scores, state["holdings"], state["cash"], raw_close, gate_on=True)
    else:
        orders = portfolio.decide(pd.Series(dtype=float), state["holdings"], state["cash"], raw_close, gate_on=False)

    orders = _merge_retry_sells(orders, state.pop("_retry_sells", []))

    ledger.save_orders(next_day, orders)
    state["pending_exec_date"] = next_day
    state["last_settled"] = today
    ledger.save_state(state)
    ledger.append_nav(today, nav, state["cash"], len(state["holdings"]), gate_on)
    n_buy = sum(1 for o in orders if o.side == "buy")
    n_sell = len(orders) - n_buy
    log(f"[nightly] {today} 完成: 净值={nav:,.0f} 持仓={len(state['holdings'])} 门控={'开' if gate_on else '关'} → {next_day} 挂单 买{n_buy}/卖{n_sell}")
    summary["orders"] = f"buy{n_buy}/sell{n_sell}"
    return summary

"""组合层：单步 TopkDropout 决策——给定分数与当前持仓，产出次日订单。

语义与研究回测一致（qlib TopkDropoutStrategy 的忠实单步版）：
每日最多换 N_DROP 只；买入预算=现金*RISK_DEGREE/买入数；参考价=信号日收盘（未复权）；
实际成交价由执行器在次日开盘决定。门控离场日=清仓单。
"""

from dataclasses import dataclass, field
from typing import Dict, List

import pandas as pd

from . import config as C


@dataclass
class Order:
    code: str
    side: str            # "buy" / "sell"
    shares: int          # 股数（整手）
    ref_price: float     # 参考价（信号日未复权收盘）
    reason: str = ""


def _lot_round(shares: float) -> int:
    return int(shares // C.LOT) * C.LOT


def decide(
    scores: pd.Series,
    holdings: Dict[str, int],
    cash: float,
    ref_prices: Dict[str, float],
    gate_on: bool,
) -> List[Order]:
    """产出次日订单。scores: index=instrument；ref_prices: 信号日未复权收盘。"""
    orders: List[Order] = []
    if not gate_on:
        for code, sh in holdings.items():
            if sh > 0:
                orders.append(Order(code, "sell", sh, ref_prices.get(code, 0.0), "gate_off_liquidate"))
        return orders

    held = [c for c, sh in holdings.items() if sh > 0]
    scores = scores.dropna()
    last = scores.reindex(held).sort_values(ascending=False)
    not_held = scores[~scores.index.isin(held)].sort_values(ascending=False)
    today = list(not_held.index[: C.N_DROP + max(C.TOPK - len(held), 0)])

    comb = pd.concat([last, scores.reindex(today)]).sort_values(ascending=False)
    bottom = list(comb.index[-C.N_DROP:]) if len(comb) > C.N_DROP else []
    sell = [c for c in last.index if c in bottom]
    buy = today[: len(sell) + max(C.TOPK - len(held), 0)]

    est_cash = cash
    for code in sell:
        px = ref_prices.get(code)
        if px and holdings.get(code, 0) > 0:
            orders.append(Order(code, "sell", holdings[code], px, "dropout"))
            est_cash += holdings[code] * px * (1 - C.CLOSE_COST - C.IMPACT_COST)

    if buy:
        budget = est_cash * C.RISK_DEGREE / len(buy)
        for code in buy:
            px = ref_prices.get(code)
            if not px or px <= 0:
                continue
            shares = _lot_round(budget / px)
            if shares >= C.LOT:
                orders.append(Order(code, "buy", shares, px, "topk_entry"))
    return orders

"""门控 V2：中证全指连续 N 天收于 MA 下方→离场；单日大涨→强制回场。

判定只用截至 asof 日的数据（无前视）；输出的是 asof 次一交易日应处的状态。
"""

from typing import Tuple

import pandas as pd

from . import config as C
from . import data


def gate_series(closes: pd.Series) -> pd.Series:
    """由指数收盘序列计算逐日门控状态（True=在场）。索引与 closes 对齐。"""
    above = closes > closes.rolling(C.GATE_MA).mean()
    surge = closes.pct_change() > C.GATE_SURGE_REENTRY
    off = ((~above).rolling(C.GATE_CONFIRM_DAYS).sum() >= C.GATE_CONFIRM_DAYS) & (~surge)
    return ~off


def gate_for_next_day(asof: str) -> Tuple[bool, str]:
    """用截至 asof 的指数数据，判定次日门控状态。返回 (在场?, 说明)。"""
    start = (pd.Timestamp(asof) - pd.Timedelta(days=C.GATE_MA * 4)).strftime("%Y-%m-%d")
    closes = data.index_closes(C.GATE_INDEX, start, asof)
    if len(closes) < C.GATE_MA + C.GATE_CONFIRM_DAYS:
        return True, "指数数据不足，默认在场"
    g = bool(gate_series(closes).iloc[-1])
    ma = closes.rolling(C.GATE_MA).mean().iloc[-1]
    note = (
        f"{C.GATE_INDEX_NAME} {closes.iloc[-1]:.1f} vs MA{C.GATE_MA} {ma:.1f}，"
        f"近{C.GATE_CONFIRM_DAYS}日线下天数={int((closes > closes.rolling(C.GATE_MA).mean()).iloc[-C.GATE_CONFIRM_DAYS:].eq(False).sum())}，"
        f"末日涨幅={closes.pct_change().iloc[-1]:+.2%} → {'在场' if g else '离场'}"
    )
    return g, note

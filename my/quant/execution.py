"""执行层：统一接口 + 影子实现。将来接 QMT 时新增 QMTExecutor 实现同一接口即可。

接口约定：
    settle(orders, exec_date) -> List[Receipt]
订单在 exec_date 开盘执行；影子实现用当日真实行情推演成交。
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Sequence

from . import config as C
from . import data
from .portfolio import Order
from .trade_planner import AccountSnapshot, MarketSnapshot, PlannedOrder


@dataclass
class Receipt:
    code: str
    side: str
    shares: int          # 实际成交股数（0=未成交）
    price: float         # 成交价（含滑点）
    cost: float          # 费用
    status: str          # filled / blocked_limit / suspended / no_data


class Executor(Protocol):
    def settle(
        self, orders: List[Order], exec_date: str, cash: float, holdings: Dict[str, int]
    ) -> List[Receipt]: ...


class ExecutionAdapter(Protocol):
    def submit_and_wait(
        self,
        orders: Sequence[PlannedOrder],
        exec_date: str,
        account: AccountSnapshot,
        market: MarketSnapshot,
        wait_seconds: int,
    ) -> List[Receipt]: ...


class ShadowExecutor:
    """影子执行：按 exec_date 真实开盘价 ± 滑点虚拟成交；
    开盘涨停禁买、开盘跌停禁卖、停牌不成交。"""

    def submit_and_wait(
        self,
        orders: Sequence[PlannedOrder],
        exec_date: str,
        account: AccountSnapshot,
        market: MarketSnapshot,
        wait_seconds: int,
    ) -> List[Receipt]:
        del market, wait_seconds
        legacy_orders = [
            Order(order.code, order.side, order.shares, order.limit_price, order.reason)
            for order in orders
        ]
        holdings = {code: holding.shares for code, holding in account.holdings.items()}
        return self.settle(legacy_orders, exec_date, account.cash, holdings)

    def settle(
        self,
        orders: List[Order],
        exec_date: str,
        cash: float,
        holdings: Optional[Dict[str, int]] = None,
    ) -> List[Receipt]:
        bars = data.day_bars(exec_date)
        out: List[Receipt] = []
        available_cash = float(cash)
        available_holdings = dict(holdings or {})
        for o in orders:
            if o.code not in bars.index:
                out.append(Receipt(o.code, o.side, 0, 0.0, 0.0, "no_data"))
                continue
            row = bars.loc[o.code]
            open_adj, prev_close, vol, factor = row["open"], row["prev_close"], row["volume"], row["factor"]
            if vol is None or vol != vol or vol <= 0 or open_adj != open_adj:
                out.append(Receipt(o.code, o.side, 0, 0.0, 0.0, "suspended"))
                continue
            chg = open_adj / prev_close - 1 if prev_close and prev_close == prev_close else 0.0
            raw_open = float(open_adj) / float(factor) if factor and factor == factor and factor > 0 else float(open_adj)
            if o.side == "buy":
                if chg > C.LIMIT_TH:
                    out.append(Receipt(o.code, "buy", 0, 0.0, 0.0, "blocked_limit"))
                    continue
                price = raw_open * (1 + C.IMPACT_COST)
                cost = max(o.shares * price * C.OPEN_COST, C.MIN_COST)
                if o.shares * price + cost > available_cash:
                    out.append(Receipt(o.code, "buy", 0, 0.0, 0.0, "insufficient_cash"))
                    continue
                out.append(Receipt(o.code, "buy", o.shares, price, cost, "filled"))
                available_cash -= o.shares * price + cost
            else:
                if o.shares > available_holdings.get(o.code, 0):
                    out.append(Receipt(o.code, "sell", 0, 0.0, 0.0, "insufficient_position"))
                    continue
                if chg < -C.LIMIT_TH:
                    out.append(Receipt(o.code, "sell", 0, 0.0, 0.0, "blocked_limit"))
                    continue
                price = raw_open * (1 - C.IMPACT_COST)
                cost = max(o.shares * price * C.CLOSE_COST, C.MIN_COST)
                out.append(Receipt(o.code, "sell", o.shares, price, cost, "filled"))
                available_cash += o.shares * price - cost
                available_holdings[o.code] -= o.shares
        return out

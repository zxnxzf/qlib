"""让 Qlib 历史回测调用共享交易规划器。"""

import copy
from typing import Dict, Optional

import pandas as pd

from qlib.backtest.decision import Order, OrderDir, TradeDecisionWO
from qlib.contrib.strategy.signal_strategy import BaseSignalStrategy

from . import config as C
from .execution import Receipt
from .signal_package import build_signal_package
from .trade_planner import (
    AccountSnapshot,
    HoldingSnapshot,
    MarketSnapshot,
    QuoteSnapshot,
    apply_receipts,
    plan_buys,
    plan_sells,
)


def raw_shares_from_qlib(amount: float, factor: float) -> int:
    if factor is None or float(factor) <= 0:
        raise ValueError("Qlib 订单缺少有效复权因子")
    raw = float(amount) * float(factor)
    nearest_lot = round(raw / C.LOT) * C.LOT
    return int(nearest_lot if abs(raw - nearest_lot) <= 2 else round(raw))


def qlib_amount_from_raw(raw_shares: int, factor: float) -> float:
    if factor is None or float(factor) <= 0:
        raise ValueError("Qlib 订单缺少有效复权因子")
    return float(raw_shares) / float(factor)


class SharedPlannerStrategy(BaseSignalStrategy):
    """Qlib 薄适配器；选股、补选和股数计算全部委托给共享规划器。"""

    def __init__(
        self,
        *,
        gate: pd.Series,
        topk: int = C.TOPK,
        n_drop: int = C.N_DROP,
        hold_thresh: int = C.HOLD_THRESH,
        factor_cache=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._gate = gate.copy()
        self._gate.index = pd.to_datetime(self._gate.index).normalize()
        self.topk = int(topk)
        self.n_drop = int(n_drop)
        self.hold_thresh = int(hold_thresh)
        self.factor_cache = factor_cache
        self.only_tradable = C.ONLY_TRADABLE
        self.recorded_orders: Dict[pd.Timestamp, list] = {}
        self.recorded_signal_dates: Dict[pd.Timestamp, str] = {}
        self.recorded_packages: Dict[pd.Timestamp, object] = {}
        self.recorded_skips: Dict[pd.Timestamp, dict] = {}
        self.recorded_accounts: Dict[pd.Timestamp, AccountSnapshot] = {}
        self._planner_account: Optional[AccountSnapshot] = None
        self._pending_exec_timestamp: Optional[pd.Timestamp] = None

    def _factor(self, code: str, start, end) -> float:
        factor = self.trade_exchange.get_factor(code, start, end)
        if (factor is None or pd.isna(factor) or float(factor) <= 0) and self.factor_cache is not None:
            factors = self.factor_cache.factors_on(pd.Timestamp(start).strftime("%Y-%m-%d"))
            if code in factors.index and pd.notna(factors.loc[code]):
                factor = factors.loc[code]
        if factor is None or pd.isna(factor) or float(factor) <= 0:
            raise ValueError(f"Qlib 缺少执行日复权因子: {code}")
        return float(factor)

    def _account(self, trade_start, trade_end) -> AccountSnapshot:
        if self._planner_account is not None:
            freq = self.trade_calendar.get_freq()
            holdings = {}
            for code, holding in self._planner_account.holdings.items():
                holdings[code] = HoldingSnapshot(
                    shares=holding.shares,
                    available_shares=holding.shares,
                    held_days=int(self.trade_position.get_stock_count(code, bar=freq)),
                )
            self._planner_account = AccountSnapshot(
                cash=self._planner_account.cash,
                holdings=holdings,
            )
            return self._planner_account

        holdings = {}
        freq = self.trade_calendar.get_freq()
        for code in self.trade_position.get_stock_list():
            factor = self._factor(code, trade_start, trade_end)
            shares = raw_shares_from_qlib(self.trade_position.get_stock_amount(code), factor)
            if shares <= 0:
                continue
            holdings[str(code)] = HoldingSnapshot(
                shares=shares,
                available_shares=shares,
                held_days=int(self.trade_position.get_stock_count(code, bar=freq)),
            )
        self._planner_account = AccountSnapshot(
            cash=float(self.trade_position.get_cash()),
            holdings=holdings,
        )
        return self._planner_account

    def _reference_closes(self, scores: pd.Series, signal_start, signal_end) -> Dict[str, float]:
        signal_date = pd.Timestamp(signal_start).strftime("%Y-%m-%d")
        if self.factor_cache is not None:
            closes = self.factor_cache.raw_closes_on(signal_date).dropna()
            return {str(code): float(close) for code, close in closes.items() if float(close) > 0}

        closes = {}
        for raw_code in scores.dropna().index:
            code = str(raw_code)
            factor = self._factor(code, signal_start, signal_end)
            close = self.trade_exchange.get_close(code, signal_start, signal_end)
            if close is not None and not pd.isna(close) and float(close) > 0:
                closes[code] = float(close) / factor
        return closes

    def _market(self, codes, exec_date: str, trade_start, trade_end) -> MarketSnapshot:
        quotes = {}
        for code in sorted(set(codes)):
            factor = self._factor(code, trade_start, trade_end)
            buy_price = self.trade_exchange.get_deal_price(
                code, trade_start, trade_end, direction=OrderDir.BUY
            )
            sell_price = self.trade_exchange.get_deal_price(
                code, trade_start, trade_end, direction=OrderDir.SELL
            )
            ask = float(buy_price) / factor if buy_price is not None and not pd.isna(buy_price) else 0.0
            bid = float(sell_price) / factor if sell_price is not None and not pd.isna(sell_price) else 0.0
            buyable = self.trade_exchange.is_stock_tradable(
                code, trade_start, trade_end, direction=OrderDir.BUY
            )
            sellable = self.trade_exchange.is_stock_tradable(
                code, trade_start, trade_end, direction=OrderDir.SELL
            )
            suspended = self.trade_exchange.check_stock_suspended(code, trade_start, trade_end)
            status = "suspended" if suspended else "normal" if buyable and sellable else "blocked_limit"
            last = ask or bid
            quotes[code] = QuoteSnapshot(
                code=code,
                timestamp=f"{exec_date}T09:30:00",
                bid1=bid,
                ask1=ask,
                last=last,
                high_limit=last * 1.1,
                low_limit=last * 0.9,
                buyable=bool(buyable),
                sellable=bool(sellable),
                status=status,
            )
        return MarketSnapshot(exec_date=exec_date, quotes=quotes)

    def _params(self) -> dict:
        return {
            "topk": self.topk,
            "candidate_limit": C.CANDIDATE_LIMIT,
            "n_drop": self.n_drop,
            "hold_thresh": self.hold_thresh,
            "risk_degree": self.risk_degree,
            "lot": C.LOT,
            "open_cost": float(getattr(self.trade_exchange, "open_cost", C.OPEN_COST)),
            "close_cost": float(getattr(self.trade_exchange, "close_cost", C.CLOSE_COST)),
            "min_cost": float(getattr(self.trade_exchange, "min_cost", C.MIN_COST)),
            "max_slippage": C.MAX_SLIPPAGE,
        }

    def _qlib_order(self, planned, trade_start, trade_end):
        factor = self._factor(planned.code, trade_start, trade_end)
        amount = qlib_amount_from_raw(planned.shares, factor)
        if planned.side == "sell":
            current_amount = float(self.trade_position.get_stock_amount(planned.code))
            if raw_shares_from_qlib(current_amount, factor) == planned.shares:
                amount = current_amount
            else:
                amount = min(amount, current_amount)
        return Order(
            stock_id=planned.code,
            amount=amount,
            start_time=trade_start,
            end_time=trade_end,
            direction=OrderDir.BUY if planned.side == "buy" else OrderDir.SELL,
        )

    def _simulate_sells(self, sell_plan, account, trade_start, trade_end):
        temporary_position = copy.deepcopy(self.trade_position)
        receipts = []
        for planned in sell_plan.orders:
            factor = self._factor(planned.code, trade_start, trade_end)
            simulated = self._qlib_order(planned, trade_start, trade_end)
            trade_value, trade_cost, trade_price = self.trade_exchange.deal_order(
                simulated, position=temporary_position
            )
            if simulated.deal_amount and abs(simulated.deal_amount - simulated.amount) <= 1e-8:
                filled = planned.shares
            else:
                filled = raw_shares_from_qlib(simulated.deal_amount, factor) if simulated.deal_amount else 0
            status = "filled" if filled >= planned.shares else "partial" if filled else "blocked"
            raw_price = float(trade_price) / factor if filled and not pd.isna(trade_price) else 0.0
            receipts.append(
                Receipt(
                    code=planned.code,
                    side="sell",
                    shares=filled,
                    price=raw_price,
                    cost=float(trade_cost),
                    status=status,
                )
            )
        return apply_receipts(account, receipts)

    def post_exe_step(self, execute_result=None) -> None:
        if self._planner_account is None or self._pending_exec_timestamp is None:
            return

        planned = {
            (order.code, order.side): order
            for order in self.recorded_orders.get(self._pending_exec_timestamp, [])
        }
        receipts = []
        for order, _trade_value, trade_cost, trade_price in execute_result or []:
            if not order.deal_amount:
                continue
            side = "buy" if order.direction == OrderDir.BUY else "sell"
            planned_order = planned.get((str(order.stock_id), side))
            factor = float(order.factor)
            if planned_order is not None and abs(order.deal_amount - order.amount) <= 1e-8:
                shares = planned_order.shares
            else:
                shares = raw_shares_from_qlib(order.deal_amount, factor)
            receipts.append(
                Receipt(
                    code=str(order.stock_id),
                    side=side,
                    shares=shares,
                    price=float(trade_price) / factor,
                    cost=float(trade_cost),
                    status="filled" if planned_order is None or shares >= planned_order.shares else "partial",
                )
            )
        self._planner_account = apply_receipts(self._planner_account, receipts)
        self.recorded_accounts[self._pending_exec_timestamp] = self._planner_account

    def generate_trade_decision(self, execute_result=None):
        del execute_result
        trade_step = self.trade_calendar.get_trade_step()
        trade_start, trade_end = self.trade_calendar.get_step_time(trade_step)
        signal_start, signal_end = self.trade_calendar.get_step_time(trade_step, shift=1)
        exec_timestamp = pd.Timestamp(trade_start).normalize()
        self._pending_exec_timestamp = exec_timestamp
        exec_date = exec_timestamp.strftime("%Y-%m-%d")
        signal_date = pd.Timestamp(signal_start).strftime("%Y-%m-%d")
        gate_on = bool(self._gate.get(exec_timestamp, True))
        scores = self.signal.get_signal(start_time=signal_start, end_time=signal_end)
        if isinstance(scores, pd.DataFrame):
            scores = scores.iloc[:, 0]
        if scores is None:
            return TradeDecisionWO([], self)
        scores = scores if gate_on else pd.Series(dtype=float)

        account = self._account(trade_start, trade_end)
        package = build_signal_package(
            scores=scores,
            signal_date=signal_date,
            exec_date=exec_date,
            gate_on=gate_on,
            holding_codes=account.holdings,
            params=self._params(),
            batch_id=f"qlib_{signal_date}_{exec_date}",
            reference_closes=self._reference_closes(scores, signal_start, signal_end) if gate_on else {},
        )
        market = self._market(
            list(account.holdings) + [candidate.code for candidate in package.candidates],
            exec_date,
            trade_start,
            trade_end,
        )
        sell_plan = plan_sells(package, account, market)
        account_after_sells = self._simulate_sells(sell_plan, account, trade_start, trade_end)
        buy_plan = plan_buys(package, account_after_sells, market)
        planned_orders = list(sell_plan.orders) + list(buy_plan.orders)
        qlib_orders = [self._qlib_order(order, trade_start, trade_end) for order in planned_orders]

        self.recorded_signal_dates[exec_timestamp] = signal_date
        self.recorded_orders[exec_timestamp] = planned_orders
        self.recorded_packages[exec_timestamp] = package
        self.recorded_skips[exec_timestamp] = {
            (skip.side, skip.code): skip.reason
            for skip in list(sell_plan.skips) + list(buy_plan.skips)
        }
        return TradeDecisionWO(qlib_orders, self)


def run_qlib_shared_planner_backtest(
    pred: pd.Series,
    gate_by_exec_date: pd.Series,
    start: str,
    end: str,
    factor_cache=None,
    signal_dates=None,
    impact_cost: Optional[float] = None,
):
    from qlib.contrib.evaluate import backtest_daily

    strategy = SharedPlannerStrategy(
        gate=gate_by_exec_date,
        signal=pred,
        topk=C.TOPK,
        n_drop=C.N_DROP,
        hold_thresh=C.HOLD_THRESH,
        factor_cache=factor_cache,
        risk_degree=C.RISK_DEGREE,
    )
    report, positions = backtest_daily(
        start_time=start,
        end_time=end,
        strategy=strategy,
        account=C.SHADOW_INIT_CASH,
        benchmark=C.BENCH,
        exchange_kwargs={
            "deal_price": C.DEAL_PRICE,
            "limit_threshold": (
                f"$open/Ref($close,1)-1 > {C.LIMIT_TH}",
                f"$open/Ref($close,1)-1 < {-C.LIMIT_TH}",
            ),
            "open_cost": C.OPEN_COST,
            "close_cost": C.CLOSE_COST,
            "min_cost": C.MIN_COST,
            "impact_cost": C.IMPACT_COST if impact_cost is None else impact_cost,
        },
    )
    from .parity import qlib_outputs_to_snapshots

    return qlib_outputs_to_snapshots(
        report,
        positions,
        strategy.recorded_orders,
        gate_by_exec_date,
        factor_cache=factor_cache,
        signal_dates=signal_dates or strategy.recorded_signal_dates,
        skips=strategy.recorded_skips,
        planner_accounts=strategy.recorded_accounts,
    )

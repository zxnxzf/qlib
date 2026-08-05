import pandas as pd

from my.quant.qlib_adapter import (
    SharedPlannerStrategy,
    qlib_amount_from_raw,
    raw_shares_from_qlib,
)


class FakeCalendar:
    def get_trade_step(self):
        return 0

    def get_step_time(self, _step=None, shift=0):
        if shift == 1:
            return pd.Timestamp("2026-08-04"), pd.Timestamp("2026-08-04 23:59:59")
        return pd.Timestamp("2026-08-05"), pd.Timestamp("2026-08-05 23:59:59")

    def get_freq(self):
        return "day"


class FakeSignal:
    def __init__(self, scores):
        self.scores = scores

    def get_signal(self, start_time, end_time):
        assert pd.Timestamp(start_time).strftime("%Y-%m-%d") == "2026-08-04"
        return self.scores


class FakePosition:
    def __init__(self, cash, amounts, counts=None):
        self.cash = cash
        self.amounts = dict(amounts)
        self.counts = counts or {code: 2 for code in amounts}

    def get_cash(self):
        return self.cash

    def get_stock_list(self):
        return list(self.amounts)

    def get_stock_amount(self, code):
        return self.amounts[code]

    def get_stock_count(self, code, bar):
        assert bar == "day"
        return self.counts[code]


class FakeAccount:
    def __init__(self, position):
        self.current_position = position


class FakeExchange:
    open_cost = 0.0005
    close_cost = 0.0015
    min_cost = 5.0

    def __init__(self, prices, factors=None, blocked_buys=()):
        self.prices = prices
        self.factors = factors or {code: 1.0 for code in prices}
        self.blocked_buys = set(blocked_buys)
        self.simulated_sells = []

    def get_factor(self, stock_id, start_time, end_time):
        return self.factors[stock_id]

    def get_close(self, stock_id, start_time, end_time):
        return self.prices[stock_id] * self.factors[stock_id]

    def get_deal_price(self, stock_id, start_time, end_time, direction):
        return self.prices[stock_id] * self.factors[stock_id]

    def is_stock_tradable(self, stock_id, start_time, end_time, direction=None):
        from qlib.backtest.decision import OrderDir

        return not (direction == OrderDir.BUY and stock_id in self.blocked_buys)

    def check_stock_suspended(self, stock_id, start_time, end_time):
        return False

    def deal_order(self, order, position=None):
        self.simulated_sells.append(order.stock_id)
        factor = self.factors[order.stock_id]
        adjusted_price = self.prices[order.stock_id] * factor
        order.deal_amount = order.amount
        order.factor = factor
        value = order.amount * adjusted_price
        return value, 5.0, adjusted_price


def _strategy(position, exchange, scores, gate_on=True, topk=1, n_drop=1):
    signal_index = pd.MultiIndex.from_product(
        [[pd.Timestamp("2026-08-04")], scores.index],
        names=["datetime", "instrument"],
    )
    strategy = SharedPlannerStrategy(
        signal=pd.Series(scores.to_numpy(), index=signal_index),
        gate=pd.Series([gate_on], index=[pd.Timestamp("2026-08-05")]),
        topk=topk,
        n_drop=n_drop,
        hold_thresh=1,
    )
    strategy.signal = FakeSignal(scores)
    strategy.level_infra = {"trade_calendar": FakeCalendar()}
    strategy.common_infra = {
        "trade_account": FakeAccount(position),
        "trade_exchange": exchange,
    }
    return strategy


def test_raw_share_and_qlib_amount_conversion_use_execution_factor():
    assert raw_shares_from_qlib(200, 0.5) == 100
    assert qlib_amount_from_raw(100, 0.5) == 200


def test_shared_strategy_uses_t_minus_one_package_and_sell_cash_before_buying():
    scores = pd.Series({"SH600001": -2.0, "SH600002": 3.0, "SZ000001": 2.0})
    position = FakePosition(cash=0.0, amounts={"SH600001": 100})
    exchange = FakeExchange(
        {"SH600001": 10.0, "SH600002": 10.0, "SZ000001": 9.0},
        blocked_buys={"SH600002"},
    )
    strategy = _strategy(position, exchange, scores)

    orders = strategy.generate_trade_decision().get_decision()

    assert [(order.stock_id, int(order.direction), order.amount) for order in orders] == [
        ("SH600001", 0, 100),
        ("SZ000001", 1, 100),
    ]
    assert exchange.simulated_sells == ["SH600001"]
    package = strategy.recorded_packages[pd.Timestamp("2026-08-05")]
    assert package.signal_date == "2026-08-04"
    assert package.exec_date == "2026-08-05"
    assert [candidate.code for candidate in package.candidates[:3]] == [
        "SH600002",
        "SZ000001",
        "SH600001",
    ]
    assert strategy.recorded_orders[pd.Timestamp("2026-08-05")][1].code == "SZ000001"


def test_shared_strategy_converts_raw_plans_back_to_adjusted_qlib_amounts():
    scores = pd.Series({"SH600001": -1.0, "SZ000001": 2.0})
    position = FakePosition(cash=0.0, amounts={"SH600001": 200})
    exchange = FakeExchange(
        {"SH600001": 10.0, "SZ000001": 9.0},
        factors={"SH600001": 0.5, "SZ000001": 0.5},
    )
    strategy = _strategy(position, exchange, scores)

    orders = strategy.generate_trade_decision().get_decision()

    assert orders[0].stock_id == "SH600001"
    assert orders[0].amount == 200
    assert strategy.recorded_orders[pd.Timestamp("2026-08-05")][0].shares == 100


def test_gate_off_liquidates_all_sellable_holdings_without_buys():
    scores = pd.Series({"SH600001": 2.0, "SH600002": 1.0})
    position = FakePosition(cash=0.0, amounts={"SH600002": 100, "SH600001": 100})
    exchange = FakeExchange({"SH600001": 10.0, "SH600002": 10.0})
    strategy = _strategy(position, exchange, scores, gate_on=False, topk=2, n_drop=1)

    orders = strategy.generate_trade_decision().get_decision()

    assert [(order.stock_id, int(order.direction)) for order in orders] == [
        ("SH600001", 0),
        ("SH600002", 0),
    ]


def test_sell_amount_never_exceeds_actual_qlib_position_after_factor_round_trip():
    actual_amount = 311.9520711532372
    factor = 100 / 311.97640561208146
    scores = pd.Series({"SH600246": 1.0})
    position = FakePosition(cash=0.0, amounts={"SH600246": actual_amount})
    exchange = FakeExchange({"SH600246": 10.0}, factors={"SH600246": factor})
    strategy = _strategy(position, exchange, scores, gate_on=False)

    order = strategy.generate_trade_decision().get_decision()[0]

    assert order.amount == actual_amount

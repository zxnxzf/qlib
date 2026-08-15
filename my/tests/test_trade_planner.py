from dataclasses import FrozenInstanceError, replace

import pytest

from my.quant.trade_planner import (
    AccountSnapshot,
    HoldingSnapshot,
    MarketSnapshot,
    QuoteSnapshot,
    SignalCandidate,
    SignalPackage,
    apply_receipts,
    plan_buys,
    plan_sells,
    price_band,
)
from my.quant.execution import Receipt


def _package(*, gate_on=True, scores=None, n_drop=2, hold_thresh=1):
    return SignalPackage(
        batch_id="2026-08-04-v1",
        signal_date="2026-08-04",
        exec_date="2026-08-05",
        gate_on=gate_on,
        candidates=(),
        holding_scores=scores or {},
        params={"n_drop": n_drop, "hold_thresh": hold_thresh},
    )


def _account(codes, *, held_days=2):
    return AccountSnapshot(
        cash=10_000.0,
        holdings={
            code: HoldingSnapshot(shares=1_000, available_shares=1_000, held_days=held_days)
            for code in codes
        },
    )


def _market(*codes, blocked=()):
    return MarketSnapshot(
        exec_date="2026-08-05",
        quotes={
            code: QuoteSnapshot(
                code=code,
                timestamp="2026-08-05T09:30:05",
                bid1=9.99,
                ask1=10.01,
                last=10.0,
                high_limit=11.0,
                low_limit=9.0,
                buyable=True,
                sellable=code not in blocked,
                status="normal" if code not in blocked else "suspended",
            )
            for code in codes
        },
    )


def test_planner_snapshots_are_immutable():
    account = _account(["SH600001"])

    with pytest.raises(FrozenInstanceError):
        account.cash = 0.0


def test_plan_sells_liquidates_all_sellable_holdings_when_gate_is_off():
    account = _account(["SH600003", "SH600001", "SH600002"])

    result = plan_sells(
        _package(gate_on=False),
        account,
        _market(*account.holdings),
    )

    assert [(order.code, order.side, order.shares) for order in result.orders] == [
        ("SH600001", "sell", 1_000),
        ("SH600002", "sell", 1_000),
        ("SH600003", "sell", 1_000),
    ]
    assert all(order.reason == "gate_off_liquidate" for order in result.orders)


def test_plan_sells_chooses_two_lowest_sellable_holdings():
    scores = {"SH600001": -3.0, "SH600002": -2.0, "SH600003": 1.0}
    account = _account(scores)

    result = plan_sells(_package(scores=scores), account, _market(*scores))

    assert [(order.code, order.side) for order in result.orders] == [
        ("SH600001", "sell"),
        ("SH600002", "sell"),
    ]


def test_plan_sells_skips_non_sellable_holding_and_uses_next_lowest():
    scores = {
        "SH600001": -3.0,
        "SH600002": -2.0,
        "SH600003": -1.0,
        "SH600004": 1.0,
    }
    account = _account(scores)

    result = plan_sells(
        _package(scores=scores),
        account,
        _market(*scores, blocked={"SH600001"}),
    )

    assert [order.code for order in result.orders] == ["SH600002", "SH600003"]
    assert [(skip.code, skip.reason) for skip in result.skips] == [
        ("SH600001", "suspended")
    ]


def test_plan_sells_never_auto_sells_holding_without_score():
    scores = {"SH600001": None, "SH600002": -2.0}
    account = _account(scores)

    result = plan_sells(_package(scores=scores, n_drop=1), account, _market(*scores))

    assert [order.code for order in result.orders] == ["SH600002"]
    assert [(skip.code, skip.reason) for skip in result.skips] == [
        ("SH600001", "missing_score")
    ]


def test_plan_sells_respects_hold_day_protection():
    scores = {"SH600001": -3.0, "SH600002": -2.0, "SH600003": -1.0}
    account = AccountSnapshot(
        cash=10_000.0,
        holdings={
            "SH600001": HoldingSnapshot(1_000, 1_000, 0),
            "SH600002": HoldingSnapshot(1_000, 1_000, 2),
            "SH600003": HoldingSnapshot(1_000, 1_000, 2),
        },
    )

    result = plan_sells(
        _package(scores=scores, hold_thresh=1),
        account,
        _market(*scores),
    )

    assert [order.code for order in result.orders] == ["SH600002", "SH600003"]
    assert [(skip.code, skip.reason) for skip in result.skips] == [
        ("SH600001", "hold_day_protection")
    ]


def test_plan_sells_breaks_equal_score_ties_by_code():
    scores = {"SH600003": 0.0, "SH600001": 0.0, "SH600002": 0.0}
    account = _account(scores)

    result = plan_sells(_package(scores=scores), account, _market(*scores))

    assert [order.code for order in result.orders] == ["SH600001", "SH600002"]


def test_plan_sells_skips_stale_quote_and_uses_next_lowest():
    scores = {"SH600001": -3.0, "SH600002": -2.0}
    account = _account(scores)
    market = _market(*scores)
    market.quotes["SH600001"] = replace(
        market.quotes["SH600001"], timestamp="2026-08-04T15:00:00+08:00"
    )

    result = plan_sells(_package(scores=scores, n_drop=1), account, market)

    assert [order.code for order in result.orders] == ["SH600002"]
    assert [(skip.code, skip.reason) for skip in result.skips] == [("SH600001", "stale_quote")]


def _buy_package(candidates, *, topk=2, risk_degree=0.95):
    return SignalPackage(
        batch_id="2026-08-04-v1",
        signal_date="2026-08-04",
        exec_date="2026-08-05",
        gate_on=True,
        candidates=tuple(
            SignalCandidate(code, score, rank, reference_close)
            for rank, (code, score, reference_close) in enumerate(candidates, start=1)
        ),
        holding_scores={},
        params={
            "topk": topk,
            "n_drop": 2,
            "risk_degree": risk_degree,
            "lot": 100,
            "open_cost": 0.0005,
            "min_cost": 5.0,
            "max_slippage": 0.003,
        },
    )


def _quote(
    code,
    *,
    bid1=9.99,
    ask1=10.01,
    high_limit=11.0,
    low_limit=9.0,
    buyable=True,
    sellable=True,
    status="normal",
    risk=False,
):
    return QuoteSnapshot(
        code=code,
        timestamp="2026-08-05T09:30:05",
        bid1=bid1,
        ask1=ask1,
        last=10.0,
        high_limit=high_limit,
        low_limit=low_limit,
        buyable=buyable,
        sellable=sellable,
        status=status,
        risk_blocked=risk,
        risk_reason="st_risk" if risk else "",
    )


def test_plan_buys_uses_actual_cash_after_completed_and_blocked_sells():
    account = AccountSnapshot(
        cash=0.0,
        holdings={
            "SH600001": HoldingSnapshot(100, 100, 2),
            "SH600002": HoldingSnapshot(100, 100, 2),
        },
    )
    updated = apply_receipts(
        account,
        [
            Receipt("SH600001", "sell", 100, 10.0, 5.0, "filled"),
            Receipt("SH600002", "sell", 0, 0.0, 0.0, "blocked_limit"),
        ],
    )
    package = _buy_package([("SZ000001", 3.0, 9.0)])
    market = MarketSnapshot("2026-08-05", {"SZ000001": _quote("SZ000001", ask1=9.0)})

    result = plan_buys(package, updated, market)

    assert updated.cash == 995.0
    assert set(updated.holdings) == {"SH600002"}
    assert [(order.code, order.shares) for order in result.orders] == [("SZ000001", 100)]


def test_plan_buys_filters_held_and_blocked_candidates_then_uses_top100_backup():
    account = AccountSnapshot(
        cash=10_000.0,
        holdings={"SH600001": HoldingSnapshot(100, 100, 2)},
    )
    package = _buy_package(
        [
            ("SH600002", 5.0, 10.0),
            ("SH600001", 4.0, 10.0),
            ("SH600003", 3.0, 10.0),
        ],
        topk=2,
    )
    market = MarketSnapshot(
        "2026-08-05",
        {
            "SH600002": _quote("SH600002", buyable=False, status="blocked_limit"),
            "SH600001": _quote("SH600001"),
            "SH600003": _quote("SH600003"),
        },
    )

    result = plan_buys(package, account, market)

    assert [order.code for order in result.orders] == ["SH600003"]
    assert result.orders[0].candidate_rank == 3
    assert [(skip.code, skip.reason) for skip in result.skips] == [
        ("SH600002", "blocked_limit"),
        ("SH600001", "already_held"),
    ]


def test_plan_buys_uses_affordable_top100_backup_when_higher_rank_is_too_expensive():
    package = _buy_package(
        [("SH600001", 2.0, 10.0), ("SH600002", 1.0, 1.0)],
        topk=1,
    )
    market = MarketSnapshot(
        "2026-08-05",
        {
            "SH600001": _quote("SH600001", ask1=10.0),
            "SH600002": _quote("SH600002", ask1=1.0, high_limit=1.1, low_limit=0.9),
        },
    )

    result = plan_buys(package, AccountSnapshot(500.0, {}), market)

    assert [(order.code, order.shares) for order in result.orders] == [("SH600002", 400)]
    assert [(skip.code, skip.reason) for skip in result.skips] == [
        ("SH600001", "insufficient_for_one_lot")
    ]


def test_plan_buys_sizes_at_protected_ceiling_including_fees():
    package = _buy_package([("SH600001", 2.0, 10.0)], topk=1, risk_degree=1.0)
    market = MarketSnapshot("2026-08-05", {"SH600001": _quote("SH600001", ask1=10.0)})

    result = plan_buys(package, AccountSnapshot(1_005.0, {}), market)

    # 100 shares fit at ask1 (1000 + 5), but not at the protected 10.03 ceiling (1003 + 5).
    assert result.orders == ()
    assert [(skip.code, skip.reason) for skip in result.skips] == [
        ("SH600001", "insufficient_for_one_lot")
    ]


def test_price_band_caps_one_reprice_to_point_three_percent_and_exchange_limits():
    assert price_band(10.0, "buy", high_limit=10.02, low_limit=9.0, max_slippage=0.003) == (
        10.0,
        10.02,
    )
    assert price_band(10.0, "sell", high_limit=11.0, low_limit=9.98, max_slippage=0.003) == (
        9.98,
        10.0,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reference", float("nan")),
        ("reference", float("inf")),
        ("high_limit", float("nan")),
        ("low_limit", float("-inf")),
        ("max_slippage", float("nan")),
    ],
)
def test_price_band_rejects_non_finite_prices(field, value):
    kwargs = {
        "reference": 10.0,
        "side": "buy",
        "high_limit": 11.0,
        "low_limit": 9.0,
        "max_slippage": 0.003,
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match="有限数值"):
        price_band(**kwargs)


def test_plan_buys_records_risk_blocked_candidate_before_backup():
    package = _buy_package(
        [("SH600001", 2.0, 10.0), ("SH600002", 1.0, 10.0)],
        topk=1,
    )
    market = MarketSnapshot(
        "2026-08-05",
        {
            "SH600001": _quote("SH600001", risk=True),
            "SH600002": _quote("SH600002"),
        },
    )

    result = plan_buys(package, AccountSnapshot(10_000.0, {}), market)

    assert [order.code for order in result.orders] == ["SH600002"]
    assert [(skip.code, skip.reason) for skip in result.skips] == [("SH600001", "st_risk")]


def test_apply_receipts_keeps_unfilled_part_of_partial_sell():
    account = AccountSnapshot(
        100.0,
        {"SH600001": HoldingSnapshot(shares=200, available_shares=200, held_days=2)},
    )

    updated = apply_receipts(
        account,
        [Receipt("SH600001", "sell", 100, 10.0, 5.0, "partial")],
    )

    assert updated.cash == 1_095.0
    assert updated.holdings["SH600001"] == HoldingSnapshot(100, 100, 2)


@pytest.mark.parametrize(
    "receipt",
    [
        Receipt("SH600001", "sell", float("nan"), 10.0, 0.0, "filled"),
        Receipt("SH600001", "sell", 100, float("nan"), 0.0, "filled"),
        Receipt("SH600001", "sell", 100, 10.0, float("inf"), "filled"),
        Receipt("SH600001", "sell", 0, float("nan"), 0.0, "blocked_limit"),
    ],
)
def test_apply_receipts_rejects_non_finite_receipt_values(receipt):
    with pytest.raises(ValueError, match="有限数值"):
        apply_receipts(_account(["SH600001"]), [receipt])


def test_planners_skip_non_finite_quote_prices():
    scores = {"SH600001": -1.0}
    sell_market = MarketSnapshot(
        "2026-08-05",
        {"SH600001": replace(_quote("SH600001"), bid1=float("nan"))},
    )
    sell_result = plan_sells(_package(scores=scores, n_drop=1), _account(scores), sell_market)

    buy_package = _buy_package([("SH600001", 1.0, 10.0)], topk=1)
    buy_market = MarketSnapshot(
        "2026-08-05",
        {"SH600001": replace(_quote("SH600001"), ask1=float("inf"))},
    )
    buy_result = plan_buys(buy_package, AccountSnapshot(10_000.0, {}), buy_market)

    assert sell_result.orders == ()
    assert [(skip.code, skip.reason) for skip in sell_result.skips] == [("SH600001", "invalid_bid1")]
    assert buy_result.orders == ()
    assert [(skip.code, skip.reason) for skip in buy_result.skips] == [("SH600001", "invalid_ask1")]

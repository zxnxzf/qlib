from dataclasses import FrozenInstanceError

import pytest

from my.quant.trade_planner import (
    AccountSnapshot,
    HoldingSnapshot,
    MarketSnapshot,
    QuoteSnapshot,
    SignalPackage,
    plan_sells,
)


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

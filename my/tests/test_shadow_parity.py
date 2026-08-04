import pandas as pd
import pytest

from my.quant.parity import (
    DailySnapshot,
    MarketCache,
    OrderSnapshot,
    compare_snapshots,
    run_shadow_replay,
    validate_snapshot_dates,
)


def test_equal_snapshots_have_full_parity():
    snap = DailySnapshot(
        date="2025-01-02",
        nav=100_000.0,
        cash=50_000.0,
        gate_on=True,
        holdings={"SH600000": 5_000},
        orders=[OrderSnapshot("SH600000", "buy", 5_000)],
        receipts={},
    )

    result = compare_snapshots({snap.date: snap}, {snap.date: snap})

    assert result.summary["daily_rows"] == 1
    assert result.summary["cash_match_rate"] == 1.0
    assert result.summary["holding_match_rate"] == 1.0
    assert result.summary["order_match_rate"] == 1.0
    assert result.daily_compare.loc[0, "nav_delta"] == 0.0


def test_snapshot_difference_is_located_by_date_and_stock():
    qlib = DailySnapshot(
        "2025-01-02",
        100_000.0,
        50_000.0,
        True,
        {"SH600000": 5_000},
        [OrderSnapshot("SZ000001", "buy", 2_000)],
        {},
    )
    shadow = DailySnapshot(
        "2025-01-02",
        99_990.0,
        49_990.0,
        True,
        {"SH600000": 4_900},
        [OrderSnapshot("SZ000001", "buy", 1_900)],
        {},
    )

    result = compare_snapshots({qlib.date: qlib}, {shadow.date: shadow})

    holding = result.holdings_compare.iloc[0]
    order = result.orders_compare.iloc[0]
    assert holding["date"] == "2025-01-02"
    assert holding["code"] == "SH600000"
    assert holding["shares_delta"] == -100
    assert order["shares_delta"] == -100
    assert result.daily_compare.loc[0, "nav_delta"] == -10.0


def test_missing_comparison_date_is_rejected():
    with pytest.raises(ValueError, match="缺少日期"):
        validate_snapshot_dates({}, ["2025-01-02", "2025-01-03"])


def test_gate_mismatch_is_reported_as_hard_error():
    qlib = DailySnapshot("2025-01-02", 100_000.0, 100_000.0, True, {}, [], {})
    shadow = DailySnapshot("2025-01-02", 100_000.0, 100_000.0, False, {}, [], {})

    with pytest.raises(ValueError, match="门控不一致"):
        compare_snapshots({qlib.date: qlib}, {shadow.date: shadow})


def test_market_cache_returns_requested_fields():
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2025-01-02"), "SH600000")],
        names=["datetime", "instrument"],
    )
    cache = MarketCache(
        pd.DataFrame(
            {
                "open": [10.0],
                "close": [10.2],
                "volume": [1_000.0],
                "factor": [1.0],
                "prev_close": [9.8],
            },
            index=index,
        )
    )

    bars = cache.day_bars("2025-01-02", fields=("$close", "$factor"))

    assert bars.columns.tolist() == ["close", "factor", "prev_close"]
    assert bars.loc["SH600000", "close"] == 10.2


def test_shadow_replay_uses_warmup_only_to_create_first_order(tmp_path):
    dates = [pd.Timestamp("2024-12-31"), pd.Timestamp("2025-01-02")]
    instruments = ["SH600000"]
    index = pd.MultiIndex.from_product(
        [dates, instruments], names=["datetime", "instrument"]
    )
    cache = MarketCache(
        pd.DataFrame(
            {
                "open": [10.0, 10.0],
                "close": [10.0, 10.2],
                "volume": [1_000.0, 1_000.0],
                "factor": [1.0, 1.0],
                "prev_close": [9.9, 10.0],
            },
            index=index,
        )
    )
    pred = pd.Series(
        [1.0, 1.0],
        index=index,
        name="score",
    )
    gates = pd.Series(
        [True, True],
        index=[pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03")],
    )

    snapshots = run_shadow_replay(
        pred=pred,
        gate_by_exec_date=gates,
        cache=cache,
        warmup="2024-12-31",
        start="2025-01-02",
        end="2025-01-02",
        state_dir=tmp_path,
        log=lambda _msg: None,
    )

    assert list(snapshots) == ["2025-01-02"]
    assert snapshots["2025-01-02"].holdings == {"SH600000": 9_500}
    assert snapshots["2025-01-02"].orders == [
        OrderSnapshot("SH600000", "buy", 9_500)
    ]
    assert snapshots["2025-01-02"].receipts == {
        ("SH600000", "buy"): "filled"
    }

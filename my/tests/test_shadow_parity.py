import pandas as pd
import pytest

import my.quant.parity as parity
from my.quant.parity import (
    DailySnapshot,
    MarketCache,
    OrderSnapshot,
    compare_snapshots,
    normalize_qlib_order,
    position_to_holdings,
    qlib_outputs_to_snapshots,
    run_shadow_replay,
    validate_snapshot_dates,
    write_parity_artifacts,
)
from my.quant.trade_planner import AccountSnapshot, HoldingSnapshot


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
    assert result.summary["holding_exact_day_match_rate"] == 1.0
    assert result.summary["order_exact_day_match_rate"] == 1.0
    assert result.summary["qlib_total_return"] == 0.0
    assert result.summary["shadow_total_return"] == 0.0
    assert result.daily_compare.loc[0, "nav_delta"] == 0.0
    assert result.summary["planner_mismatch_count"] == 0


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
    assert order["classification"] == "rounding_or_cost"
    assert result.daily_compare.loc[0, "nav_delta"] == -10.0


def test_summary_counts_shadow_receipt_statuses():
    qlib = DailySnapshot("2025-01-02", 100_000.0, 100_000.0, True, {}, [], {})
    shadow = DailySnapshot(
        "2025-01-02",
        100_000.0,
        100_000.0,
        True,
        {},
        [],
        {("SH600000", "buy"): "blocked_limit"},
    )

    result = compare_snapshots({qlib.date: qlib}, {shadow.date: shadow})

    assert result.summary["shadow_receipt_status_counts"] == {"blocked_limit": 1}
    assert result.summary["direct_rejection_count"] == 1


def test_order_rows_name_the_sell_or_buy_stage():
    qlib = DailySnapshot(
        "2025-01-02", 100_000.0, 99_000.0, True, {}, [OrderSnapshot("SH600000", "buy", 100)], {}
    )
    shadow = DailySnapshot(
        "2025-01-02", 100_000.0, 99_000.0, True, {}, [OrderSnapshot("SH600000", "buy", 100)], {}
    )

    result = compare_snapshots({qlib.date: qlib}, {shadow.date: shadow})

    assert result.orders_compare.loc[0, "stage"] == "buy"


def test_candidate_skip_reasons_are_compared_between_adapters():
    skips = {("buy", "SH600001"): "blocked_limit", ("buy", "SZ000001"): "already_held"}
    qlib = DailySnapshot("2025-01-02", 100_000.0, 100_000.0, True, {}, [], {}, skips=skips)
    shadow = DailySnapshot("2025-01-02", 100_000.0, 100_000.0, True, {}, [], {}, skips=skips)

    result = compare_snapshots({qlib.date: qlib}, {shadow.date: shadow})

    assert result.summary["candidate_skip_reason_counts"] == {
        "already_held": 1,
        "blocked_limit": 1,
    }
    assert result.summary["skip_mismatch_count"] == 0
    assert result.skips_compare["match"].all()


def test_blocked_shadow_order_is_classified_with_stock_example():
    qlib = DailySnapshot("2025-01-02", 100_000.0, 100_000.0, True, {}, [], {})
    shadow = DailySnapshot(
        "2025-01-02",
        100_000.0,
        100_000.0,
        True,
        {},
        [OrderSnapshot("SH600000", "buy", 1_000)],
        {("SH600000", "buy"): "blocked_limit"},
    )

    result = compare_snapshots({qlib.date: qlib}, {shadow.date: shadow})

    assert result.orders_compare.loc[0, "classification"] == "execution_tradability"
    assert result.summary["order_mismatch_class_counts"]["execution_tradability"] == 1
    assert result.summary["mismatch_examples"]["execution_tradability"] == [
        {
            "date": "2025-01-02",
            "code": "SH600000",
            "side": "buy",
            "shares_delta": 1_000,
            "shadow_status": "blocked_limit",
        }
    ]


def test_insufficient_cash_is_classified_as_sizing_or_cost():
    qlib = DailySnapshot("2025-01-02", 100_000.0, 100_000.0, True, {}, [], {})
    shadow = DailySnapshot(
        "2025-01-02",
        100_000.0,
        100_000.0,
        True,
        {},
        [OrderSnapshot("SH600000", "buy", 1_000)],
        {("SH600000", "buy"): "insufficient_cash"},
    )

    result = compare_snapshots({qlib.date: qlib}, {shadow.date: shadow})

    assert result.orders_compare.loc[0, "classification"] == "rounding_or_cost"


@pytest.mark.parametrize("shadow_status", ["", "filled"])
def test_one_sided_order_without_direct_block_evidence_is_path_dependency(shadow_status):
    qlib = DailySnapshot(
        "2025-01-03",
        100_000.0,
        90_000.0,
        True,
        {"SH600000": 1_000},
        [],
        {},
    )
    receipts = {("SZ000001", "buy"): shadow_status} if shadow_status else {}
    shadow = DailySnapshot(
        "2025-01-03",
        100_000.0,
        90_000.0,
        True,
        {"SZ000001": 1_000},
        [OrderSnapshot("SZ000001", "buy", 1_000)],
        receipts,
    )

    result = compare_snapshots({qlib.date: qlib}, {shadow.date: shadow})

    assert result.orders_compare.loc[0, "classification"] == "selection_or_path_dependency"


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


def test_market_cache_carries_last_factor_after_instrument_disappears():
    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2025-01-02"), "SZ300379"),
            (pd.Timestamp("2025-01-03"), "SH600000"),
        ],
        names=["datetime", "instrument"],
    )
    cache = MarketCache(
        pd.DataFrame(
            {
                "open": [10.0, 8.0],
                "close": [10.2, 8.1],
                "volume": [1_000.0, 2_000.0],
                "factor": [0.2, 0.5],
                "prev_close": [9.8, 7.9],
            },
            index=index,
        )
    )

    assert cache.factors_on("2025-01-03").loc["SZ300379"] == pytest.approx(0.2)


def test_market_cache_carries_last_raw_close_through_suspension():
    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2025-01-02"), "SZ300307"),
            (pd.Timestamp("2025-01-03"), "SZ300307"),
        ],
        names=["datetime", "instrument"],
    )
    cache = MarketCache(
        pd.DataFrame(
            {
                "open": [0.74, float("nan")],
                "close": [0.75, float("nan")],
                "volume": [1_000.0, 0.0],
                "factor": [0.075, 0.075],
                "prev_close": [0.73, 0.75],
            },
            index=index,
        )
    )

    assert cache.raw_closes_on("2025-01-03").loc["SZ300307"] == pytest.approx(10.0)


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
    assert snapshots["2025-01-02"].holdings == {"SH600000": 9_400}
    assert snapshots["2025-01-02"].orders == [
        OrderSnapshot("SH600000", "buy", 9_400)
    ]
    assert snapshots["2025-01-02"].receipts == {
        ("SH600000", "buy"): "filled"
    }


def test_shadow_replay_can_disable_price_impact_for_cost_control(tmp_path):
    dates = [pd.Timestamp("2024-12-31"), pd.Timestamp("2025-01-02")]
    index = pd.MultiIndex.from_product(
        [dates, ["SH600000"]], names=["datetime", "instrument"]
    )
    cache = MarketCache(
        pd.DataFrame(
            {
                "open": [10.0, 10.0],
                "close": [10.0, 10.0],
                "volume": [1_000.0, 1_000.0],
                "factor": [1.0, 1.0],
                "prev_close": [9.9, 10.0],
            },
            index=index,
        )
    )
    pred = pd.Series(1.0, index=index, name="score")
    gates = pd.Series([True, True], index=pd.to_datetime(["2025-01-02", "2025-01-03"]))

    run_shadow_replay(
        pred=pred,
        gate_by_exec_date=gates,
        cache=cache,
        warmup="2024-12-31",
        start="2025-01-02",
        end="2025-01-02",
        state_dir=tmp_path,
        impact_cost=0.0,
        log=lambda _msg: None,
    )

    receipt = pd.read_csv(tmp_path / "receipts" / "2025-01-02_buy.csv")
    assert receipt.loc[0, "price"] == pytest.approx(10.0)


def test_position_to_holdings_excludes_cash_fields():
    class FakePosition:
        def get_stock_list(self):
            return ["SH600000"]

        def get_stock_amount(self, code):
            assert code == "SH600000"
            return 1_000

    assert position_to_holdings(FakePosition()) == {"SH600000": 1_000}


def test_position_and_order_amounts_are_converted_to_raw_shares():
    from qlib.backtest.decision import OrderDir

    class FakePosition:
        def get_stock_list(self):
            return ["SH600000"]

        def get_stock_amount(self, _code):
            return 2_000

    class FakeOrder:
        stock_id = "SH600000"
        direction = OrderDir.BUY
        amount = 2_000
        factor = None

    factors = pd.Series({"SH600000": 0.5})

    assert position_to_holdings(FakePosition(), factors) == {"SH600000": 1_000}
    assert normalize_qlib_order(FakeOrder(), factor=0.5) == OrderSnapshot(
        "SH600000", "buy", 1_000
    )


def test_factor_drift_is_rounded_back_to_a_share_lot():
    class FakePosition:
        def get_stock_list(self):
            return ["SH600000"]

        def get_stock_amount(self, _code):
            return 2_000

    # 2000 * 0.4995 = 999; Qlib's trade unit is still one 100-share lot.
    assert position_to_holdings(
        FakePosition(), pd.Series({"SH600000": 0.4995})
    ) == {"SH600000": 1_000}


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(999, 1_000), (1_101, 1_100), (353, 353), (426, 426)],
)
def test_only_tiny_factor_drift_is_absorbed_into_a_share_lot(amount, expected):
    class FakePosition:
        def get_stock_list(self):
            return ["SH600000"]

        def get_stock_amount(self, _code):
            return amount

    assert position_to_holdings(
        FakePosition(), pd.Series({"SH600000": 1.0})
    ) == {"SH600000": expected}


def test_qlib_buy_order_is_normalized():
    from qlib.backtest.decision import OrderDir

    class FakeOrder:
        stock_id = "SH600000"
        direction = OrderDir.BUY
        amount = 1_000

    assert normalize_qlib_order(FakeOrder()) == OrderSnapshot(
        "SH600000", "buy", 1_000
    )


def test_artifact_writer_creates_all_required_files(tmp_path):
    snap = DailySnapshot("2025-01-02", 100_000.0, 100_000.0, True, {}, [], {})
    result = compare_snapshots({snap.date: snap}, {snap.date: snap})

    write_parity_artifacts(
        result,
        tmp_path,
        {"start": "2025-01-02", "end": "2026-07-28"},
    )

    assert {path.name for path in tmp_path.iterdir()} == {
        "daily_compare.csv",
        "holdings_compare.csv",
        "orders_compare.csv",
        "skips_compare.csv",
        "summary.json",
        "report.md",
    }
    report = (tmp_path / "report.md").read_text()
    assert "共享规划器订单差异" in report
    assert "Qlib 的 `impact_cost`" in report


def test_strict_cost_report_names_zero_impact_control(tmp_path):
    snap = DailySnapshot("2025-01-02", 100_000.0, 100_000.0, True, {}, [], {})
    result = compare_snapshots({snap.date: snap}, {snap.date: snap})

    write_parity_artifacts(
        result,
        tmp_path,
        {
            "start": "2025-01-02",
            "end": "2025-01-02",
            "cost_mode": "strict_zero_impact_control",
        },
    )

    assert "strict_zero_impact_control" in (tmp_path / "report.md").read_text()


def test_qlib_outputs_are_converted_to_daily_snapshots():
    date = pd.Timestamp("2025-01-02")

    class FakePosition:
        def get_stock_list(self):
            return ["SH600000"]

        def get_stock_amount(self, code):
            assert code == "SH600000"
            return 1_000

    report = pd.DataFrame({"account": [100_100.0], "cash": [90_000.0]}, index=[date])
    positions = {date: FakePosition()}
    orders = {date: [OrderSnapshot("SH600000", "buy", 1_000)]}
    gates = pd.Series([True], index=[date])

    snapshots = qlib_outputs_to_snapshots(
        report,
        positions,
        orders,
        gates,
        signal_dates={date: "2024-12-31"},
    )

    assert snapshots["2025-01-02"] == DailySnapshot(
        "2025-01-02",
        100_100.0,
        90_000.0,
        True,
        {"SH600000": 1_000},
        [OrderSnapshot("SH600000", "buy", 1_000)],
        {},
        "2024-12-31",
    )


def test_qlib_snapshot_prefers_recorded_planner_account_over_factor_reinterpretation():
    date = pd.Timestamp("2025-01-02")

    class DriftedPosition:
        def get_stock_list(self):
            return ["SZ300117"]

        def get_stock_amount(self, _code):
            return 200.0

    report = pd.DataFrame({"account": [2_010.0], "cash": [995.0]}, index=[date])
    planner_account = AccountSnapshot(
        cash=995.0,
        holdings={"SZ300117": HoldingSnapshot(100, 100, 1)},
    )

    snapshots = qlib_outputs_to_snapshots(
        report,
        {date: DriftedPosition()},
        {date: []},
        pd.Series([True], index=[date]),
        factor_cache=type(
            "FactorCache",
            (),
            {"factors_on": lambda _self, _date: pd.Series({"SZ300117": 0.489})},
        )(),
        planner_accounts={date: planner_account},
    )

    assert snapshots["2025-01-02"].cash == 995.0
    assert snapshots["2025-01-02"].holdings == {"SZ300117": 100}


def test_signal_date_mismatch_is_a_hard_error():
    qlib = DailySnapshot(
        "2025-01-02", 100_000.0, 100_000.0, True, {}, [], {}, "2024-12-31"
    )
    shadow = DailySnapshot(
        "2025-01-02", 100_000.0, 100_000.0, True, {}, [], {}, "2024-12-30"
    )

    with pytest.raises(ValueError, match="信号日期不一致"):
        compare_snapshots({qlib.date: qlib}, {shadow.date: shadow})


def _validation_cache(dates):
    index = pd.MultiIndex.from_product(
        [pd.to_datetime(dates), ["SH600000"]],
        names=["datetime", "instrument"],
    )
    return MarketCache(
        pd.DataFrame(
            {
                "open": 10.0,
                "close": 10.0,
                "volume": 1_000.0,
                "factor": 1.0,
                "prev_close": 9.9,
            },
            index=index,
        )
    )


def test_replay_inputs_require_prediction_for_every_previous_trade_day():
    calendar = ["2024-12-31", "2025-01-02", "2025-01-03"]
    pred_index = pd.MultiIndex.from_product(
        [[pd.Timestamp("2024-12-31")], ["SH600000"]],
        names=["datetime", "instrument"],
    )
    pred = pd.Series([1.0], index=pred_index)
    gates = pd.Series(
        [True, False],
        index=pd.to_datetime(["2025-01-02", "2025-01-03"]),
    )

    with pytest.raises(ValueError, match="预测缺少信号日: 2025-01-02"):
        parity.validate_replay_inputs(
            pred,
            gates,
            _validation_cache(calendar),
            calendar,
            warmup="2024-12-31",
            start="2025-01-02",
            end="2025-01-03",
        )


def test_replay_inputs_require_warmup_to_be_previous_trade_day():
    calendar = ["2024-12-30", "2024-12-31", "2025-01-02"]
    pred_index = pd.MultiIndex.from_product(
        [pd.to_datetime(calendar), ["SH600000"]],
        names=["datetime", "instrument"],
    )
    pred = pd.Series(1.0, index=pred_index)
    gates = pd.Series([True], index=[pd.Timestamp("2025-01-02")])

    with pytest.raises(ValueError, match="预热日必须是 start 的前一交易日"):
        parity.validate_replay_inputs(
            pred,
            gates,
            _validation_cache(calendar),
            calendar,
            warmup="2024-12-30",
            start="2025-01-02",
            end="2025-01-02",
        )


def test_replay_inputs_reject_missing_or_empty_gate_values():
    calendar = ["2024-12-31", "2025-01-02"]
    pred_index = pd.MultiIndex.from_product(
        [[pd.Timestamp("2024-12-31")], ["SH600000"]],
        names=["datetime", "instrument"],
    )
    pred = pd.Series([1.0], index=pred_index)
    gates = pd.Series([float("nan")], index=[pd.Timestamp("2025-01-02")])

    with pytest.raises(ValueError, match="门控缺少或为空: 2025-01-02"):
        parity.validate_replay_inputs(
            pred,
            gates,
            _validation_cache(calendar),
            calendar,
            warmup="2024-12-31",
            start="2025-01-02",
            end="2025-01-02",
        )


def test_qlib_adapter_passes_locked_execution_semantics(monkeypatch):
    import qlib.contrib.evaluate

    from my.quant.qlib_adapter import SharedPlannerStrategy

    date = pd.Timestamp("2025-01-02")
    captured = {}

    class FakePosition:
        def get_stock_list(self):
            return []

    def fake_backtest_daily(**kwargs):
        captured.update(kwargs)
        strategy = kwargs["strategy"]
        strategy.recorded_signal_dates[date] = "2024-12-31"
        return (
            pd.DataFrame({"account": [100_000.0], "cash": [100_000.0]}, index=[date]),
            {date: FakePosition()},
        )

    monkeypatch.setattr(qlib.contrib.evaluate, "backtest_daily", fake_backtest_daily)
    pred_index = pd.MultiIndex.from_product(
        [[pd.Timestamp("2024-12-31")], ["SH600000"]],
        names=["datetime", "instrument"],
    )
    pred = pd.Series([1.0], index=pred_index)
    gates = pd.Series([True], index=[date])

    snapshots = parity.run_qlib_backtest(
        pred,
        gates,
        "2025-01-02",
        "2025-01-02",
        impact_cost=0.0,
    )

    assert captured["start_time"] == "2025-01-02"
    assert captured["end_time"] == "2025-01-02"
    assert captured["exchange_kwargs"]["deal_price"] == "open"
    assert captured["exchange_kwargs"]["impact_cost"] == 0.0
    assert isinstance(captured["strategy"], SharedPlannerStrategy)
    assert captured["strategy"].only_tradable is True
    assert snapshots["2025-01-02"].signal_date == "2024-12-31"


def test_reproducibility_metadata_contains_input_hash_and_cost_semantics(tmp_path):
    from my.scripts.compare_shadow_backtest import build_repro_metadata

    prediction = tmp_path / "pred.pkl"
    prediction.write_bytes(b"prediction-input")
    cache = _validation_cache(["2024-12-31", "2025-01-02"])

    metadata = build_repro_metadata(
        start="2025-01-02",
        end="2025-01-02",
        warmup="2024-12-31",
        prediction_path=prediction,
        state_dir=tmp_path / "state",
        cache=cache,
        cost_mode="strict_zero_impact_control",
    )

    assert metadata["prediction_sha256"]
    assert metadata["market_latest_date"] == "2025-01-02"
    assert metadata["cost_mode"] == "strict_zero_impact_control"
    assert metadata["qlib_impact_cost"] == 0.0
    assert metadata["shadow_price_impact"] == 0.0

import math
import json
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest

from my.qmt.performance import QMTPerformanceLedger, register_cash_flow, update_performance
from my.qmt.protocol import ProtocolValidationError, with_checksum


def _result(batch_id, exec_date, *, fill=True, fee=False, account_after_total=999999.0):
    order_id = "%s:sell:1" % batch_id
    fills = []
    if fill:
        fill_record = {
            "order_id": order_id,
            "broker_order_id": "broker-%s" % batch_id,
            "fill_id": "fill-%s" % batch_id,
            "code": "SH600000",
            "side": "sell",
            "shares": 100,
            "price": 9.90,
        }
        if fee:
            fill_record.update(
                {"commission": 1.0, "tax": 1.0, "transfer_fee": 0.1, "other_fee": 0.0}
            )
        fills.append(fill_record)
    return with_checksum({
        "schema_version": 1,
        "batch_id": batch_id,
        "signal_date": "2026-08-14",
        "exec_date": exec_date,
        "planner_version": "shared-planner-v1",
        "started_at": "%sT09:30:00+08:00" % exec_date,
        "finished_at": "%sT09:31:00+08:00" % exec_date,
        "status": "completed",
        "reason": "",
        "account_before": {
            "cash": 100000.0,
            "market_value": 0.0,
            "total_asset": 100000.0,
            "holdings": [],
        },
        "market_snapshot": {},
        "buy_market_snapshot": {"SH600000": {"status": "normal"}},
        "sell_stage": {
            "terminal": True,
            "planned": [
                {
                    "order_id": order_id,
                    "code": "SH600000",
                    "side": "sell",
                    "shares": 100,
                    "limit_price": 10.0,
                }
            ],
            "broker_orders": [
                {
                    "order_id": order_id,
                    "broker_order_id": "broker-%s" % batch_id,
                    "code": "SH600000",
                    "side": "sell",
                    "shares": 100,
                    "price": 10.0,
                    "status": "filled" if fill else "cancelled",
                }
            ],
            "skipped": [],
            "fills": fills,
            "cancelled": [] if fill else [{"order_id": order_id, "shares": 100}],
            "errors": [],
        },
        "account_after_sell": {
            "cash": 100000.0,
            "market_value": 0.0,
            "total_asset": 100000.0,
            "holdings": [],
        },
        "buy_stage": {
            "terminal": True,
            "planned": [],
            "skipped": [],
            "broker_orders": [],
            "fills": [],
            "cancelled": [],
            "errors": [],
        },
        "account_after": {
            "cash": account_after_total,
            "market_value": 0.0,
            "total_asset": account_after_total,
            "holdings": [],
        },
        "errors": [],
    })


def _eod(batch_id, exec_date, total_asset, *, external_cash_flow=0.0, source="broker_qmt"):
    return with_checksum({
        "schema_version": 1,
        "batch_id": batch_id,
        "exec_date": exec_date,
        "snapshot_at": "%sT15:01:00+08:00" % exec_date,
        "cash": total_asset if total_asset is not None else 123.0,
        "frozen_cash": 0.0,
        "market_value": 0.0,
        "total_asset": total_asset,
        "holdings": [],
        "external_cash_flow": external_cash_flow,
        "source": source,
    })


def test_flow_adjusted_nav_benchmark_drawdown_and_idempotency(tmp_path):
    first = update_performance(
        _result("batch-1", "2026-08-17"),
        _eod("batch-1", "2026-08-17", 100000.0),
        tmp_path,
        {"2026-08-17": 0.001, "2026-08-18": 0.005},
    )
    assert first["nav"].iloc[0]["daily_return"] == 0.0
    assert first["nav"].iloc[0]["cumulative_return"] == 0.0

    second_result = _result("batch-2", "2026-08-18", fill=False)
    second = update_performance(
        second_result,
        _eod("batch-2", "2026-08-18", 111000.0, external_cash_flow=10000.0),
        tmp_path,
        {"2026-08-17": 0.001, "2026-08-18": 0.005},
    )
    last = second["nav"].iloc[-1]
    assert last["daily_return"] == pytest.approx(0.01)
    assert last["cumulative_return"] == pytest.approx(0.01)
    assert last["current_drawdown"] == pytest.approx(0.0)
    assert last["max_drawdown"] == pytest.approx(0.0)
    assert last["benchmark_cumulative_return"] == pytest.approx(0.005)
    assert last["cumulative_excess_return"] == pytest.approx(0.005)

    # Re-importing exactly the same day does not duplicate NAV, fills or flows.
    repeated = update_performance(
        second_result,
        _eod("batch-2", "2026-08-18", 111000.0, external_cash_flow=10000.0),
        tmp_path,
        {"2026-08-17": 0.001, "2026-08-18": 0.005},
    )
    assert len(repeated["nav"]) == 2
    assert len(repeated["trades"]) == 2  # one fill plus one cancelled/unfilled order
    assert len(repeated["cash_flows"]) == 2
    assert repeated["trades"].duplicated(["batch_id", "order_id", "fill_id"]).sum() == 0

    falling = update_performance(
        _result("batch-3", "2026-08-19", fill=False),
        _eod("batch-3", "2026-08-19", 99900.0),
        tmp_path,
        {"2026-08-17": 0.001, "2026-08-18": 0.005, "2026-08-19": -0.002},
    )["nav"].iloc[-1]
    assert falling["daily_return"] == pytest.approx(-0.10)
    assert falling["cumulative_return"] == pytest.approx(-0.091)
    assert falling["current_drawdown"] == pytest.approx(-0.10)
    assert falling["max_drawdown"] == pytest.approx(-0.10)


def test_unknown_fee_remains_unknown_and_sell_slippage_is_adverse(tmp_path):
    ledgers = update_performance(
        _result("batch-1", "2026-08-17", fee=False),
        _eod("batch-1", "2026-08-17", 100000.0),
        tmp_path,
    )
    trade = ledgers["trades"].iloc[0]
    assert trade["fee_status"] == "unknown"
    assert pd.isna(trade["total_fee"])
    assert trade["raw_price_slippage"] == pytest.approx(-0.01)
    assert trade["slippage"] == pytest.approx(0.01)
    assert trade["slippage_bps"] == pytest.approx(100.0)

    # Unknown values also remain empty after a CSV round trip.
    persisted = pd.read_csv(tmp_path / "qmt_trades.csv", encoding="utf-8-sig")
    assert persisted.loc[0, "fee_status"] == "unknown"
    assert pd.isna(persisted.loc[0, "total_fee"])


def test_known_fee_components_are_summed_without_simulated_rates(tmp_path):
    trade = update_performance(
        _result("batch-1", "2026-08-17", fee=True),
        _eod("batch-1", "2026-08-17", 100000.0),
        tmp_path,
    )["trades"].iloc[0]
    assert trade["fee_status"] == "known"
    assert trade["total_fee"] == pytest.approx(2.1)


def test_current_qmt_result_fields_map_reference_submit_price_and_cost(tmp_path):
    result = _result("batch-1", "2026-08-17")
    planned = result["sell_stage"]["planned"][0]
    planned.pop("limit_price")
    planned["reference_price"] = 10.0
    planned["submit_price"] = 9.97
    result["sell_stage"]["broker_orders"][0].pop("price")
    result["sell_stage"]["fills"][0]["cost"] = 0.0
    result = with_checksum(result)

    trade = update_performance(
        result,
        _eod("batch-1", "2026-08-17", 100000.0),
        tmp_path,
    )["trades"].iloc[0]
    assert trade["planned_price"] == 10.0
    assert trade["submitted_price"] == 9.97
    assert trade["total_fee"] == 0.0
    assert trade["fee_status"] == "known"


def test_missing_broker_total_asset_is_explicit_and_never_uses_result_estimate(tmp_path):
    result = _result("batch-1", "2026-08-17", account_after_total=999999.0)
    row = update_performance(
        result,
        _eod("batch-1", "2026-08-17", None),
        tmp_path,
    )["nav"].iloc[0]
    assert row["nav_status"] == "missing"
    assert pd.isna(row["total_asset"])
    assert pd.isna(row["daily_return"])
    assert "broker_total_asset_missing" in row["note"]
    missing_report = (tmp_path / "qmt_report.html").read_text(encoding="utf-8")
    assert "<strong>missing</strong>" in missing_report
    assert "没有使用影子账户" in missing_report
    assert "999,999.00" not in missing_report

    with pytest.raises(ProtocolValidationError, match="source"):
        update_performance(
            _result("batch-2", "2026-08-18"),
            _eod("batch-2", "2026-08-18", 888888.0, source="shadow"),
            tmp_path,
        )


def test_missing_eod_creates_missing_row_then_real_eod_replaces_it(tmp_path):
    result = _result("batch-1", "2026-08-17")
    missing = update_performance(result, tmp_path / "does-not-exist.json", tmp_path)["nav"]
    assert len(missing) == 1
    assert missing.iloc[0]["nav_status"] == "missing"
    assert "eod_snapshot_missing" in missing.iloc[0]["note"]

    recovered = update_performance(
        result,
        _eod("batch-1", "2026-08-17", 100000.0),
        tmp_path,
    )["nav"]
    assert len(recovered) == 1
    assert recovered.iloc[0]["nav_status"] == "ok"
    assert recovered.iloc[0]["total_asset"] == 100000.0


def test_manual_flow_fallback_and_broker_flow_precedence(tmp_path):
    ledger = QMTPerformanceLedger(tmp_path)
    ledger.ingest(
        _result("batch-1", "2026-08-17"),
        _eod("batch-1", "2026-08-17", 100000.0, external_cash_flow=None),
    )
    ledger.register_cash_flow("2026-08-18", 10000.0, flow_id="deposit-1", note="test deposit")
    manual = ledger.ingest(
        _result("batch-2", "2026-08-18"),
        _eod("batch-2", "2026-08-18", 111000.0, external_cash_flow=None),
    )
    manual_row = manual["nav"].iloc[-1]
    assert manual_row["external_cash_flow"] == 10000.0
    assert manual_row["cash_flow_source"] == "manual"
    assert manual_row["daily_return"] == pytest.approx(0.01)

    broker = ledger.ingest(
        _result("batch-2", "2026-08-18"),
        _eod("batch-2", "2026-08-18", 111000.0, external_cash_flow=8000.0),
    )
    broker_row = broker["nav"].iloc[-1]
    assert broker_row["external_cash_flow"] == 8000.0
    assert broker_row["cash_flow_source"] == "broker_qmt"
    assert broker_row["daily_return"] == pytest.approx(0.03)
    assert "manual_cash_flow_ignored_broker_preferred" in broker_row["anomaly"]
    assert set(broker["cash_flows"]["flow_id"]) == {"deposit-1", "broker:batch-2"}


def test_reimport_replaces_batch_fills_instead_of_accumulating_stale_fills(tmp_path):
    result = _result("batch-1", "2026-08-17")
    update_performance(result, _eod("batch-1", "2026-08-17", 100000.0), tmp_path)

    result["sell_stage"]["fills"][0]["fill_id"] = "corrected-fill"
    result["sell_stage"]["fills"][0]["price"] = 9.80
    result = with_checksum(result)
    trades = update_performance(result, None, tmp_path)["trades"]
    assert len(trades) == 1
    assert trades.iloc[0]["fill_id"] == "corrected-fill"
    assert trades.iloc[0]["fill_price"] == pytest.approx(9.80)


def test_result_and_eod_must_describe_same_day_and_batch(tmp_path):
    with pytest.raises(ValueError, match="exec_date mismatch"):
        update_performance(
            _result("batch-1", "2026-08-17"),
            _eod("batch-1", "2026-08-18", 100000.0),
            tmp_path,
        )
    with pytest.raises(ValueError, match="batch_id mismatch"):
        update_performance(
            _result("batch-1", "2026-08-17"),
            _eod("batch-X", "2026-08-17", 100000.0),
            tmp_path,
        )


def test_register_cash_flow_is_idempotent_by_flow_id(tmp_path):
    register_cash_flow("2026-08-17", 1000.0, tmp_path, flow_id="deposit")
    frame = register_cash_flow("2026-08-17", 2000.0, tmp_path, flow_id="deposit")
    assert len(frame) == 1
    assert frame.iloc[0]["amount"] == 2000.0
    assert math.isfinite(frame.iloc[0]["amount"])


def test_update_generates_self_contained_html_with_core_metrics_and_recent_rows(tmp_path):
    update_performance(
        _result("batch-1", "2026-08-17", fee=False),
        _eod("batch-1", "2026-08-17", 100000.0),
        tmp_path,
        {"2026-08-17": 0.001, "2026-08-18": 0.002},
    )
    update_performance(
        _result("batch-2", "2026-08-18", fee=True),
        _eod("batch-2", "2026-08-18", 101000.0),
        tmp_path,
        {"2026-08-17": 0.001, "2026-08-18": 0.002},
    )

    report_path = tmp_path / "qmt_report.html"
    assert report_path.exists()
    assert not (tmp_path / "qmt_report.html.tmp").exists()
    report = report_path.read_text(encoding="utf-8")
    for label in (
        "最新总资产",
        "累计收益",
        "当前回撤",
        "最大回撤",
        "基准累计收益",
        "累计超额",
        "成交数",
        "已知费用",
        "未知费用数",
        "最近净值",
        "最近订单与成交",
    ):
        assert label in report
    assert "101,000.00" in report
    assert "1.00%" in report
    assert "0.20%" in report
    assert "0.80%" in report
    assert "2.10" in report
    assert "SH600000" in report
    assert "http://" not in report
    assert "https://" not in report


def test_mapping_and_file_inputs_receive_full_protocol_validation(tmp_path):
    invalid_result = _result("batch-1", "2026-08-17")
    invalid_result["status"] = "looks_completed"
    invalid_result = with_checksum(invalid_result)
    with pytest.raises(ProtocolValidationError, match="status"):
        update_performance(invalid_result, _eod("batch-1", "2026-08-17", 100000.0), tmp_path)

    invalid_eod = _eod("batch-1", "2026-08-17", 100000.0)
    invalid_eod["schema_version"] = 999
    invalid_eod = with_checksum(invalid_eod)
    invalid_eod_path = tmp_path / "invalid-eod.json"
    invalid_eod_path.write_text(json.dumps(invalid_eod), encoding="utf-8")
    with pytest.raises(ProtocolValidationError, match="schema_version"):
        update_performance(_result("batch-1", "2026-08-17"), invalid_eod_path, tmp_path)

    tampered_result = _result("batch-1", "2026-08-17")
    tampered_result["account_after"]["total_asset"] = 123.0
    with pytest.raises(ValueError, match="checksum"):
        update_performance(tampered_result, _eod("batch-1", "2026-08-17", 100000.0), tmp_path)


def test_invalid_manual_cash_flow_hard_fails_without_mutating_new_batch(tmp_path):
    update_performance(
        _result("batch-1", "2026-08-17"),
        _eod("batch-1", "2026-08-17", 100000.0),
        tmp_path,
    )
    pd.DataFrame(
        [
            {
                "exec_date": "2026-08-18",
                "flow_id": "valid-flow",
                "amount": 1000.0,
                "source": "manual",
                "status": "confirmed",
                "note": "valid row must not hide the typo",
            },
            {
                "exec_date": "2026-08-18",
                "flow_id": "bad-flow",
                "amount": "not-a-number",
                "source": "manual",
                "status": "confirmed",
                "note": "typo",
            }
        ]
    ).to_csv(tmp_path / "qmt_cash_flows.csv", index=False, encoding="utf-8-sig")

    with pytest.raises(ValueError, match="invalid amount"):
        update_performance(
            _result("batch-2", "2026-08-18"),
            _eod("batch-2", "2026-08-18", 120000.0, external_cash_flow=None),
            tmp_path,
        )
    trades = pd.read_csv(tmp_path / "qmt_trades.csv", encoding="utf-8-sig")
    assert set(trades["batch_id"]) == {"batch-1"}


def test_missing_broker_flow_uses_explicit_dedicated_account_assumption(tmp_path):
    row = update_performance(
        _result("batch-1", "2026-08-17"),
        _eod("batch-1", "2026-08-17", 100000.0, external_cash_flow=None),
        tmp_path,
    )["nav"].iloc[0]
    assert row["external_cash_flow"] == 0.0
    assert row["cash_flow_source"] == "assumed_zero_dedicated_account"
    assert "external_cash_flow_unavailable_assumed_zero_dedicated_account" in row["anomaly"]


def test_valid_nav_after_missing_row_resumes_from_last_broker_fact(tmp_path):
    benchmark = {"2026-08-17": 0.001, "2026-08-18": 0.002, "2026-08-19": 0.003}
    update_performance(
        _result("batch-1", "2026-08-17"),
        _eod("batch-1", "2026-08-17", 100000.0),
        tmp_path,
        benchmark,
    )
    update_performance(
        _result("batch-2", "2026-08-18"),
        _eod("batch-2", "2026-08-18", None, external_cash_flow=5000.0),
        tmp_path,
        benchmark,
    )
    nav = update_performance(
        _result("batch-3", "2026-08-19"),
        _eod("batch-3", "2026-08-19", 106000.0),
        tmp_path,
        benchmark,
    )["nav"]

    assert pd.isna(nav.iloc[1]["daily_return"])
    assert nav.iloc[2]["daily_return"] == pytest.approx(0.01)
    assert nav.iloc[2]["cumulative_return"] == pytest.approx(0.01)
    assert nav.iloc[2]["benchmark_cumulative_return"] == pytest.approx((1.002 * 1.003) - 1.0)
    assert "return_spans_1_missing_nav_row" in nav.iloc[2]["anomaly"]


def test_new_batch_for_same_exec_date_replaces_old_batch_trades(tmp_path):
    update_performance(
        _result("batch-old", "2026-08-17"),
        _eod("batch-old", "2026-08-17", 100000.0),
        tmp_path,
    )
    ledgers = update_performance(
        _result("batch-new", "2026-08-17"),
        _eod("batch-new", "2026-08-17", 100100.0),
        tmp_path,
    )
    assert set(ledgers["trades"]["batch_id"]) == {"batch-new"}
    assert list(ledgers["nav"]["batch_id"]) == ["batch-new"]


@pytest.mark.parametrize("side", ["result", "eod"])
def test_new_batch_for_same_date_rejects_partial_replacement(tmp_path, side):
    update_performance(
        _result("batch-old", "2026-08-17"),
        _eod("batch-old", "2026-08-17", 100000.0),
        tmp_path,
    )

    result = _result("batch-new", "2026-08-17") if side == "result" else None
    eod = _eod("batch-new", "2026-08-17", 100100.0) if side == "eod" else None
    with pytest.raises(ValueError, match="partial same-day batch replacement"):
        update_performance(result, eod, tmp_path)

    nav = pd.read_csv(tmp_path / "qmt_nav.csv", encoding="utf-8-sig")
    trades = pd.read_csv(tmp_path / "qmt_trades.csv", encoding="utf-8-sig")
    assert set(nav["batch_id"]) == {"batch-old"}
    assert set(trades["batch_id"]) == {"batch-old"}


def test_concurrent_manual_flow_registration_does_not_lose_rows(tmp_path):
    def add_flow(index):
        register_cash_flow(
            "2026-08-17",
            float(index + 1),
            tmp_path,
            flow_id="flow-%02d" % index,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(add_flow, range(12)))

    flows = pd.read_csv(tmp_path / "qmt_cash_flows.csv", encoding="utf-8-sig")
    assert len(flows) == 12
    assert set(flows["flow_id"]) == {"flow-%02d" % index for index in range(12)}
    assert not (tmp_path / ".qmt_performance.lock").exists()


def test_unmatched_unfilled_broker_orders_are_preserved_and_flagged(tmp_path):
    result = _result("batch-1", "2026-08-17", fill=False)
    result["sell_stage"]["broker_orders"].extend(
        [
            {
                "broker_order_id": "external-1",
                "code": "SH600001",
                "side": "buy",
                "shares": 100,
                "status": "submitted",
            },
            {
                "broker_order_id": "external-2",
                "code": "SH600002",
                "side": "sell",
                "shares": 200,
                "status": "cancelled",
            },
        ]
    )
    result = with_checksum(result)

    trades = update_performance(
        result,
        _eod("batch-1", "2026-08-17", 100000.0),
        tmp_path,
    )["trades"]
    external = trades[trades["broker_order_id"].astype(str).str.startswith("external-")]

    assert len(external) == 2
    assert set(external["order_id"]) == {
        "unmatched:broker:external-1",
        "unmatched:broker:external-2",
    }
    assert set(external["anomaly"]) == {"unknown_order_id"}


def test_unmatched_order_and_fill_join_by_broker_id(tmp_path):
    result = _result("batch-1", "2026-08-17", fill=False)
    result["sell_stage"]["broker_orders"].append(
        {
            "broker_order_id": "external-1",
            "code": "SH600001",
            "side": "buy",
            "shares": 100,
            "status": "filled",
        }
    )
    result["sell_stage"]["fills"].append(
        {
            "broker_order_id": "external-1",
            "fill_id": "external-fill-1",
            "code": "SH600001",
            "side": "buy",
            "shares": 100,
            "price": 10.0,
        }
    )
    result = with_checksum(result)

    trades = update_performance(
        result,
        _eod("batch-1", "2026-08-17", 100000.0),
        tmp_path,
    )["trades"]
    external = trades[trades["broker_order_id"] == "external-1"]

    assert len(external) == 1
    assert external.iloc[0]["fill_id"] == "external-fill-1"
    assert external.iloc[0]["anomaly"] == "unknown_order_id"


def test_two_unmatched_orders_without_any_id_do_not_merge(tmp_path):
    result = _result("batch-1", "2026-08-17", fill=False)
    result["sell_stage"]["broker_orders"].extend(
        [
            {"code": "SH600001", "side": "buy", "shares": 100, "status": "submitted"},
            {"code": "SH600002", "side": "sell", "shares": 100, "status": "submitted"},
        ]
    )
    result = with_checksum(result)

    first = update_performance(
        result,
        _eod("batch-1", "2026-08-17", 100000.0),
        tmp_path,
    )["trades"]
    second = update_performance(
        result,
        _eod("batch-1", "2026-08-17", 100000.0),
        tmp_path,
    )["trades"]
    unknown = second[second["order_id"].astype(str).str.startswith("unmatched:order:")]

    assert len(unknown) == 2
    assert len(set(unknown["order_id"])) == 2
    assert len(second) == len(first)
    assert set(unknown["anomaly"]) == {"unknown_order_id"}

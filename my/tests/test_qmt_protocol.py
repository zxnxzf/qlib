import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from my.qmt import protocol


SHANGHAI = timezone(timedelta(hours=8))
EXEC_DATE = "2099-01-05"
BATCH_ID = "2099-01-04_2099-01-05_2099Q1"


def _signal(**overrides):
    payload = {
        "schema_version": protocol.SIGNAL_SCHEMA_VERSION,
        "batch_id": BATCH_ID,
        "signal_date": "2099-01-04",
        "exec_date": EXEC_DATE,
        "created_at": "2099-01-04T20:35:12+08:00",
        "expires_at": "2099-01-05T09:31:00+08:00",
        "account_alias": "qmt_sim",
        "data_asof": "2099-01-04",
        "provenance": {
            "source_type": "published_model",
            "strategy_id": "lgb_alpha158_gate905_v1",
            "release_id": "2099Q1",
            "model_sha256": "1" * 64,
            "config_sha256": "2" * 64,
            "runtime_code_sha256": "3" * 64,
            "source_git_commit": "4" * 40,
        },
        "planner_version": protocol.PLANNER_VERSION,
        "gate": {"on": True, "note": "中证500门控开启"},
        "params": {
            "topk": 50,
            "candidate_limit": 100,
            "n_drop": 2,
            "hold_thresh": 1,
            "risk_degree": 0.95,
            "lot": 100,
            "open_cost": 0.0005,
            "close_cost": 0.0015,
            "min_cost": 5.0,
            "max_slippage": 0.003,
            "wait_seconds": 30,
        },
        "scores": {"SH600000": 0.013421},
        "candidates": [
            {"rank": 1, "code": "SH600000", "score": 0.013421, "reference_close": 11.26}
        ],
    }
    payload.update(overrides)
    return payload


def _result(**overrides):
    account = {
        "cash": 5200.0,
        "market_value": 11250.0,
        "total_asset": 16450.0,
        "holdings": [],
    }
    payload = {
        "schema_version": protocol.RESULT_SCHEMA_VERSION,
        "batch_id": BATCH_ID,
        "signal_date": "2099-01-04",
        "exec_date": EXEC_DATE,
        "planner_version": protocol.PLANNER_VERSION,
        "started_at": "2099-01-05T09:30:02+08:00",
        "finished_at": "2099-01-05T09:31:04+08:00",
        "status": "completed",
        "reason": "",
        "account_before": dict(account),
        "market_snapshot": {},
        "buy_market_snapshot": {"SH600000": {"status": "normal"}},
        "sell_stage": {
            "terminal": True,
            "planned": [],
            "skipped": [],
            "broker_orders": [],
            "fills": [],
            "cancelled": [],
            "errors": [],
        },
        "account_after_sell": dict(account),
        "buy_stage": {
            "terminal": True,
            "planned": [],
            "skipped": [],
            "broker_orders": [],
            "fills": [],
            "cancelled": [],
            "errors": [],
        },
        "account_after": dict(account),
        "errors": [],
    }
    payload.update(overrides)
    return payload


def _eod(**overrides):
    payload = {
        "schema_version": protocol.EOD_SCHEMA_VERSION,
        "batch_id": BATCH_ID,
        "exec_date": EXEC_DATE,
        "snapshot_at": "2099-01-05T15:01:00+08:00",
        "cash": 5200.0,
        "frozen_cash": 0.0,
        "market_value": 11250.0,
        "total_asset": 16450.0,
        "holdings": [],
        "external_cash_flow": None,
        "source": "broker_qmt",
    }
    payload.update(overrides)
    return payload


def _before_expiry():
    return datetime(2099, 1, 5, 9, 30, tzinfo=SHANGHAI)


def test_signal_round_trip_uses_canonical_checksum_and_expected_path(tmp_path):
    path = protocol.write_signal(tmp_path, _signal())
    restored = protocol.read_signal(
        tmp_path,
        EXEC_DATE,
        expected_account_alias="qmt_sim",
        expected_planner_version=protocol.PLANNER_VERSION,
        now=_before_expiry(),
    )

    assert path == tmp_path.resolve() / "qmt_inbox" / EXEC_DATE / "signal.json"
    assert restored["gate"]["note"] == "中证500门控开启"
    assert restored["checksum"] == protocol.compute_checksum(restored)
    assert not path.with_name("signal.json.tmp").exists()
    assert not path.with_name("signal.json.lock").exists()

    original = path.read_bytes()
    assert protocol.write_signal(tmp_path, _signal()) == path
    assert path.read_bytes() == original


def test_signal_rejects_checksum_tampering(tmp_path):
    path = protocol.write_signal(tmp_path, _signal())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["gate"]["on"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(protocol.ChecksumError, match="checksum"):
        protocol.read_signal(
            tmp_path,
            EXEC_DATE,
            expected_account_alias="qmt_sim",
            expected_planner_version=protocol.PLANNER_VERSION,
            now=_before_expiry(),
        )


def test_signal_rejects_expired_payload(tmp_path):
    protocol.write_signal(tmp_path, _signal())

    with pytest.raises(protocol.ExpiredSignalError, match="expired"):
        protocol.read_signal(
            tmp_path,
            EXEC_DATE,
            expected_account_alias="qmt_sim",
            expected_planner_version=protocol.PLANNER_VERSION,
            now=datetime(2099, 1, 5, 9, 31, tzinfo=SHANGHAI),
        )


def test_signal_rejects_date_mismatch_even_when_file_is_in_requested_directory(tmp_path):
    wrong_exec_date = "2099-01-06"
    signed = protocol.with_checksum(_signal())
    protocol.atomic_write_json(protocol.signal_path(tmp_path, wrong_exec_date), signed)

    with pytest.raises(protocol.ProtocolValidationError, match="requested execution date"):
        protocol.read_signal(
            tmp_path,
            wrong_exec_date,
            expected_account_alias="qmt_sim",
            expected_planner_version=protocol.PLANNER_VERSION,
            now=_before_expiry(),
        )


@pytest.mark.parametrize(
    ("account_alias", "planner_version", "message"),
    [
        ("another_account", protocol.PLANNER_VERSION, "account_alias"),
        ("qmt_sim", "shared-planner-v0", "planner_version"),
    ],
)
def test_signal_rejects_runtime_identity_mismatch(tmp_path, account_alias, planner_version, message):
    protocol.write_signal(tmp_path, _signal())

    with pytest.raises(protocol.ProtocolValidationError, match=message):
        protocol.read_signal(
            tmp_path,
            EXEC_DATE,
            expected_account_alias=account_alias,
            expected_planner_version=planner_version,
            now=_before_expiry(),
        )


def test_signal_rejects_naive_expiry_timestamp(tmp_path):
    with pytest.raises(protocol.ProtocolValidationError, match="timezone"):
        protocol.write_signal(tmp_path, _signal(expires_at="2099-01-05T09:31:00"))


def test_signal_rejects_schema_and_data_asof_mismatch(tmp_path):
    with pytest.raises(protocol.ProtocolValidationError, match="schema_version"):
        protocol.write_signal(tmp_path, _signal(schema_version=1))
    with pytest.raises(protocol.ProtocolValidationError, match="data_asof"):
        protocol.write_signal(tmp_path, _signal(data_asof="2099-01-03"))


def test_signal_rejects_non_boolean_gate_and_invalid_parameters(tmp_path):
    payload = _signal()
    payload["gate"]["on"] = 1
    with pytest.raises(protocol.ProtocolValidationError, match="gate.on"):
        protocol.write_signal(tmp_path, payload)

    invalid_params = [
        ("candidate_limit", 99),
        ("max_slippage", 0.0031),
        ("wait_seconds", 31),
        ("risk_degree", 0.0),
        ("lot", 0),
    ]
    for field, value in invalid_params:
        payload = _signal()
        payload["params"][field] = value
        with pytest.raises(protocol.ProtocolValidationError, match=field):
            protocol.write_signal(tmp_path, payload)

    payload = _signal()
    del payload["params"]["hold_thresh"]
    with pytest.raises(protocol.ProtocolValidationError, match="hold_thresh"):
        protocol.write_signal(tmp_path, payload)


@pytest.mark.parametrize("bad_score", [float("nan"), float("inf"), float("-inf")])
def test_signal_rejects_non_finite_scores(tmp_path, bad_score):
    payload = _signal()
    payload["scores"]["SH600000"] = bad_score
    payload["candidates"][0]["score"] = bad_score

    with pytest.raises(protocol.ProtocolValidationError):
        protocol.write_signal(tmp_path, payload)


def test_signal_rejects_empty_score_code_and_candidate_structure_errors(tmp_path):
    payload = _signal(scores={"": 1.0}, candidates=[])
    with pytest.raises(protocol.ProtocolValidationError, match="scores keys"):
        protocol.write_signal(tmp_path, payload)

    mutations = []

    def discontinuous_rank(payload):
        payload["candidates"][0]["rank"] = 2

    mutations.append((discontinuous_rank, "continuous"))

    def missing_score(payload):
        payload["candidates"][0]["code"] = "SH600001"

    mutations.append((missing_score, "missing from scores"))

    def mismatched_score(payload):
        payload["candidates"][0]["score"] = 99.0

    mutations.append((mismatched_score, "does not match"))

    def invalid_close(payload):
        payload["candidates"][0]["reference_close"] = 0.0

    mutations.append((invalid_close, "positive"))

    def duplicate_code(payload):
        payload["scores"]["SH600001"] = 0.01
        payload["candidates"].append(
            {"rank": 2, "code": "SH600000", "score": 0.013421, "reference_close": 11.26}
        )

    mutations.append((duplicate_code, "unique"))

    for mutate, message in mutations:
        payload = _signal()
        mutate(payload)
        with pytest.raises(protocol.ProtocolValidationError, match=message):
            protocol.write_signal(tmp_path, payload)

    scores = {"SH%06d" % index: float(index) for index in range(101)}
    candidates = [
        {"rank": index + 1, "code": code, "score": score, "reference_close": 10.0}
        for index, (code, score) in enumerate(scores.items())
    ]
    with pytest.raises(protocol.ProtocolValidationError, match="complete ranked Top100"):
        protocol.write_signal(tmp_path, _signal(scores=scores, candidates=candidates))


def test_signal_requires_complete_strictly_ranked_top100_with_code_tiebreak(tmp_path):
    scores = {"SH600003": 3.0, "SH600002": 2.0, "SH600001": 2.0}
    correctly_ranked = [
        {"rank": 1, "code": "SH600003", "score": 3.0, "reference_close": 10.0},
        {"rank": 2, "code": "SH600001", "score": 2.0, "reference_close": 10.0},
        {"rank": 3, "code": "SH600002", "score": 2.0, "reference_close": 10.0},
    ]
    protocol.write_signal(tmp_path / "valid", _signal(scores=scores, candidates=correctly_ranked))

    with pytest.raises(protocol.ProtocolValidationError, match="complete ranked Top100"):
        protocol.write_signal(tmp_path / "missing", _signal(scores=scores, candidates=correctly_ranked[:2]))

    reversed_tie = [correctly_ranked[0], correctly_ranked[2], correctly_ranked[1]]
    reversed_tie[1] = {**reversed_tie[1], "rank": 2}
    reversed_tie[2] = {**reversed_tie[2], "rank": 3}
    with pytest.raises(protocol.ProtocolValidationError, match="score-descending/code-ascending"):
        protocol.write_signal(tmp_path / "wrong-order", _signal(scores=scores, candidates=reversed_tie))


def test_signal_rejects_invalid_published_model_provenance(tmp_path):
    payload = _signal()
    del payload["provenance"]["model_sha256"]
    with pytest.raises(protocol.ProtocolValidationError, match="model_sha256"):
        protocol.write_signal(tmp_path, payload)

    payload = _signal()
    payload["provenance"]["runtime_code_sha256"] = "not-a-sha"
    with pytest.raises(protocol.ProtocolValidationError, match="runtime_code_sha256"):
        protocol.write_signal(tmp_path, payload)

    payload = _signal()
    payload["provenance"]["source_git_commit"] = "abc123"
    with pytest.raises(protocol.ProtocolValidationError, match="source_git_commit"):
        protocol.write_signal(tmp_path, payload)


def test_immutable_path_rejects_different_payload(tmp_path):
    protocol.write_signal(tmp_path, _signal())

    with pytest.raises(protocol.DuplicatePayloadError, match="overwrite"):
        protocol.write_signal(tmp_path, _signal(batch_id="different-batch"))


def test_atomic_write_cleans_temporary_file_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "payload.json"

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(protocol.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        protocol.atomic_write_json(target, {"message": "完整写入"})

    assert not target.exists()
    assert not target.with_name("payload.json.tmp").exists()
    assert not target.with_name("payload.json.lock").exists()


def test_atomic_write_reuses_existing_content_before_waiting_on_stale_lock(tmp_path):
    target = tmp_path / "payload.json"
    payload = {"message": "already published"}
    assert protocol.atomic_write_json(target, payload) is True
    lock_path = target.with_name("payload.json.lock")
    lock_path.write_text("simulated crashed owner", encoding="utf-8")

    assert protocol.atomic_write_json(target, payload) is False


def test_atomic_write_recovers_old_lock_when_final_file_is_missing(tmp_path):
    target = tmp_path / "payload.json"
    lock_path = target.with_name("payload.json.lock")
    lock_path.write_text("dead owner", encoding="utf-8")
    old = time.time() - protocol._STALE_LOCK_SECONDS - 1
    os.utime(lock_path, (old, old))

    assert protocol.atomic_write_json(target, {"message": "recovered"}) is True
    assert json.loads(target.read_text(encoding="utf-8")) == {"message": "recovered"}
    assert not lock_path.exists()


def test_stale_lock_recovery_never_steals_lock_from_live_owner(tmp_path):
    lock_path = tmp_path / "payload.json.lock"
    lock_path.write_text(json.dumps({"pid": os.getpid(), "token": "live"}), encoding="ascii")
    old = time.time() - protocol._STALE_LOCK_SECONDS - 1
    os.utime(lock_path, (old, old))

    assert protocol._recover_stale_lock(lock_path) is False
    assert lock_path.exists()


def test_result_and_eod_round_trip_validate_batch_date_and_checksum(tmp_path):
    protocol.write_result(tmp_path, _result())
    protocol.write_eod_snapshot(tmp_path, _eod())

    result = protocol.read_result(
        tmp_path,
        EXEC_DATE,
        expected_batch_id=BATCH_ID,
        expected_planner_version=protocol.PLANNER_VERSION,
    )
    eod = protocol.read_eod_snapshot(tmp_path, EXEC_DATE, expected_batch_id=BATCH_ID)

    assert result["status"] == "completed"
    assert result["checksum"] == protocol.compute_checksum(result)
    assert eod["source"] == "broker_qmt"
    assert eod["checksum"] == protocol.compute_checksum(eod)

    with pytest.raises(protocol.ProtocolValidationError, match="batch_id"):
        protocol.read_result(tmp_path, EXEC_DATE, expected_batch_id="wrong-batch")
    with pytest.raises(protocol.ProtocolValidationError, match="batch_id"):
        protocol.read_eod_snapshot(tmp_path, EXEC_DATE, expected_batch_id="wrong-batch")


def test_result_and_eod_reject_unsupported_schema(tmp_path):
    with pytest.raises(protocol.ProtocolValidationError, match="schema_version"):
        protocol.write_result(tmp_path, _result(schema_version=2))
    with pytest.raises(protocol.ProtocolValidationError, match="schema_version"):
        protocol.write_eod_snapshot(tmp_path, _eod(schema_version=2))


def test_result_rejects_malformed_stage_and_account_holdings(tmp_path):
    payload = _result()
    del payload["sell_stage"]["fills"]
    with pytest.raises(protocol.ProtocolValidationError, match="fills"):
        protocol.write_result(tmp_path, payload)

    payload = _result()
    payload["buy_stage"]["errors"] = {}
    with pytest.raises(protocol.ProtocolValidationError, match="buy_stage.errors"):
        protocol.write_result(tmp_path, payload)

    payload = _result()
    payload["account_before"]["holdings"] = {}
    with pytest.raises(protocol.ProtocolValidationError, match="account_before.holdings"):
        protocol.write_result(tmp_path, payload)


@pytest.mark.parametrize("failure", ["duplicate_fill", "excess_fill", "wrong_code"])
def test_result_rejects_inconsistent_broker_facts(tmp_path, failure):
    payload = _result()
    order = {
        "order_id": "batch:sell:001",
        "code": "SH600000",
        "side": "sell",
        "shares": 100,
    }
    fill = {
        "fill_id": "fill-1",
        "order_id": order["order_id"],
        "code": order["code"],
        "side": "sell",
        "shares": 100,
        "price": 10.0,
    }
    payload["sell_stage"]["planned"] = [order]
    payload["sell_stage"]["fills"] = [fill]
    if failure == "duplicate_fill":
        payload["sell_stage"]["fills"].append(dict(fill))
    elif failure == "excess_fill":
        fill["shares"] = 101
    else:
        fill["code"] = "SH600001"

    with pytest.raises(protocol.ProtocolValidationError):
        protocol.write_result(tmp_path, payload)


@pytest.mark.parametrize("status", ["completed", "partial"])
@pytest.mark.parametrize("field", ["account_before", "account_after_sell", "account_after"])
def test_completed_and_partial_result_require_all_account_snapshots(tmp_path, status, field):
    payload = _result(status=status, reason="incomplete" if status == "partial" else "")
    payload[field] = {}

    with pytest.raises(protocol.ProtocolValidationError, match=field):
        protocol.write_result(tmp_path, payload)


def test_aborted_result_allows_only_unreached_account_stages_to_be_empty():
    payload = _result(status="aborted", reason="manual_reconciliation_required")
    payload["account_after_sell"] = {}
    payload["buy_stage"] = {
        "terminal": False,
        "planned": [],
        "skipped": [],
        "broker_orders": [],
        "fills": [],
        "cancelled": [],
        "errors": [],
    }
    protocol.validate_result(protocol.with_checksum(payload))

    payload["buy_stage"]["planned"] = [{"order_id": "should-not-exist"}]
    with pytest.raises(protocol.ProtocolValidationError, match="buy-stage activity"):
        protocol.validate_result(protocol.with_checksum(payload))

    payload = _result(status="aborted", reason="preflight_failed")
    payload["account_before"] = {}
    payload["account_after_sell"] = {}
    payload["account_after"] = {}
    payload["sell_stage"]["errors"] = [{"reason": "query failed"}]
    with pytest.raises(protocol.ProtocolValidationError, match="stage activity"):
        protocol.validate_result(protocol.with_checksum(payload))


def test_completed_result_requires_buy_snapshot_and_terminal_proof():
    payload = _result()
    payload.pop("buy_market_snapshot")
    with pytest.raises(protocol.ProtocolValidationError, match="buy_market_snapshot"):
        protocol.validate_result(protocol.with_checksum(payload))

    payload = _result(buy_market_snapshot=[])
    with pytest.raises(protocol.ProtocolValidationError, match="buy_market_snapshot"):
        protocol.validate_result(protocol.with_checksum(payload))

    payload = _result(buy_market_snapshot={})
    with pytest.raises(protocol.ProtocolValidationError, match="requires buy_market_snapshot"):
        protocol.validate_result(protocol.with_checksum(payload))

    payload = _result()
    payload["buy_stage"]["terminal"] = False
    with pytest.raises(protocol.ProtocolValidationError, match="proven terminal"):
        protocol.validate_result(protocol.with_checksum(payload))


@pytest.mark.parametrize("field", ["cash", "frozen_cash", "market_value", "total_asset", "external_cash_flow"])
@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_eod_rejects_non_finite_numbers(tmp_path, field, bad_value):
    payload = _eod()
    payload[field] = bad_value

    with pytest.raises(protocol.ProtocolValidationError):
        protocol.write_eod_snapshot(tmp_path, payload)


def test_eod_protocol_rejects_preclose_or_non_shanghai_snapshot():
    with pytest.raises(protocol.ProtocolValidationError, match="before 15:00"):
        protocol.validate_eod_snapshot(
            protocol.with_checksum(_eod(snapshot_at="2099-01-05T14:59:59+08:00"))
        )

    with pytest.raises(protocol.ProtocolValidationError, match="Asia/Shanghai"):
        protocol.validate_eod_snapshot(
            protocol.with_checksum(_eod(snapshot_at="2099-01-05T07:01:00+00:00"))
        )


@pytest.mark.parametrize(
    ("writer", "path_builder", "reader", "payload"),
    [
        (protocol.write_result, protocol.result_path, protocol.read_result, _result()),
        (protocol.write_eod_snapshot, protocol.eod_snapshot_path, protocol.read_eod_snapshot, _eod()),
    ],
)
def test_result_and_eod_reject_tampering(tmp_path, writer, path_builder, reader, payload):
    writer(tmp_path, payload)
    path = path_builder(tmp_path, EXEC_DATE)
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["batch_id"] = "tampered"
    path.write_text(json.dumps(stored), encoding="utf-8")

    with pytest.raises(protocol.ChecksumError, match="checksum"):
        reader(tmp_path, EXEC_DATE)

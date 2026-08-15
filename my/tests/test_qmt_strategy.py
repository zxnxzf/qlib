from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest

from my.qmt import PLANNER_VERSION
from my.qmt.protocol import eod_snapshot_path, result_path, with_checksum, write_signal
from my.qmt.qmt_strategy import (
    ExecutionAlreadyRunningError,
    QmtExecutionEngine,
    QmtExecutionError,
    RecoveryRequiredError,
    _normalize_outcome,
    _state_path,
    load_broker_bridge,
    load_local_config,
    write_eod_from_broker,
)


TZ = timezone(timedelta(hours=8))


def _signal():
    return with_checksum(
        {
            "schema_version": 2,
            "batch_id": "2026-08-14_2026-08-17_2026Q3",
            "signal_date": "2026-08-14",
            "exec_date": "2026-08-17",
            "created_at": "2026-08-14T20:00:00+08:00",
            "expires_at": "2026-08-17T09:31:00+08:00",
            "account_alias": "qmt_sim",
            "data_asof": "2026-08-14",
            "provenance": {
                "source_type": "published_model",
                "strategy_id": "test",
                "release_id": "2026Q3",
                "model_sha256": "1" * 64,
                "config_sha256": "2" * 64,
                "runtime_code_sha256": "3" * 64,
                "source_git_commit": "4" * 40,
            },
            "planner_version": PLANNER_VERSION,
            "gate": {"on": True},
            "params": {
                "topk": 2,
                "candidate_limit": 100,
                "n_drop": 1,
                "hold_thresh": 1,
                "risk_degree": 0.95,
                "lot": 100,
                "open_cost": 0.0,
                "close_cost": 0.0,
                "min_cost": 0.0,
                "max_slippage": 0.003,
                "wait_seconds": 30,
            },
            "scores": {"SH600001": -1.0, "SH600002": 2.0, "SH600003": 1.0},
            "candidates": [
                {"rank": 1, "code": "SH600002", "score": 2.0, "reference_close": 10.0},
                {"rank": 2, "code": "SH600003", "score": 1.0, "reference_close": 10.0},
                {"rank": 3, "code": "SH600001", "score": -1.0, "reference_close": 10.0},
            ],
        }
    )


class FakeBroker:
    def __init__(self, partial_sell=False):
        self.cash = 2_200.0
        self.holdings = {"SH600001": [100, 100, 2]}
        self.calls = []
        self.partial_sell = partial_sell
        self.recovery = {}

    def account_snapshot(self):
        market_value = sum(row[0] * 10.0 for row in self.holdings.values())
        return {
            "cash": self.cash,
            "frozen_cash": 0.0,
            "market_value": market_value,
            "total_asset": self.cash + market_value,
            "holdings": [
                {
                    "code": code,
                    "shares": row[0],
                    "available_shares": row[1],
                    "held_days": row[2],
                    "market_value": row[0] * 10.0,
                }
                for code, row in sorted(self.holdings.items())
            ],
            "source": "broker_qmt",
        }

    def market_snapshot(self, codes, exec_date):
        return {
            code: {
                "timestamp": exec_date + "T09:30:05+08:00",
                "bid1": 9.99,
                "ask1": 10.01,
                "last": 10.0,
                "high_limit": 11.0,
                "low_limit": 9.0,
                "buyable": True,
                "sellable": True,
                "status": "normal",
            }
            for code in codes
        }

    def execute_stage(self, stage, orders, wait_seconds):
        self.calls.append(("execute", stage, [row["order_id"] for row in orders], wait_seconds))
        fills = []
        for index, order in enumerate(orders, 1):
            shares = order["shares"]
            if stage == "sell" and self.partial_sell:
                shares //= 2
            if stage == "sell":
                held = self.holdings[order["code"]][0]
                shares = min(shares, held)
                self.holdings[order["code"]][0] -= shares
                self.holdings[order["code"]][1] -= shares
                self.cash += shares * order["submit_price"]
                if self.holdings[order["code"]][0] == 0:
                    del self.holdings[order["code"]]
            else:
                self.cash -= shares * order["submit_price"]
                self.holdings[order["code"]] = [shares, 0, 0]
            fills.append(
                {
                    "fill_id": "%s-%d" % (stage, index),
                    "order_id": order["order_id"],
                    "code": order["code"],
                    "side": stage,
                    "shares": shares,
                    "price": order["submit_price"],
                    "cost": 0.0,
                }
            )
        outcome = {
            "terminal": True,
            "broker_orders": [{"order_id": row["order_id"], "status": "terminal"} for row in orders],
            "fills": fills,
            "cancelled": [],
            "errors": [],
        }
        self.recovery[stage] = outcome
        return outcome

    def recover_stage(self, stage, orders, wait_seconds):
        self.calls.append(("recover", stage, [row["order_id"] for row in orders], wait_seconds))
        return self.recovery.get(stage)


def _now():
    return datetime(2026, 8, 17, 9, 30, 10, tzinfo=TZ)


def test_engine_runs_sell_before_buy_and_is_idempotent(tmp_path):
    broker = FakeBroker()
    engine = QmtExecutionEngine(tmp_path, "qmt_sim", broker, now_fn=_now)

    result = engine.run("2026-08-17", _signal())
    again = engine.run("2026-08-17", _signal())

    assert result == again
    assert result["status"] == "completed"
    assert [call[1] for call in broker.calls] == ["sell", "buy"]
    assert result["sell_stage"]["planned"][0]["code"] == "SH600001"
    assert result["buy_stage"]["planned"][0]["code"] == "SH600002"
    assert result["account_after_sell"]["cash"] > result["account_before"]["cash"]


def test_engine_date_lock_prevents_concurrent_duplicate_submission(tmp_path):
    entered = Event()
    release = Event()

    class BlockingBroker(FakeBroker):
        def execute_stage(self, stage, orders, wait_seconds):
            if stage == "sell":
                entered.set()
                assert release.wait(timeout=2.0)
            return super().execute_stage(stage, orders, wait_seconds)

    broker = BlockingBroker()
    first = QmtExecutionEngine(
        tmp_path,
        "qmt_sim",
        broker,
        now_fn=_now,
        lock_timeout_seconds=0.05,
    )
    second = QmtExecutionEngine(
        tmp_path,
        "qmt_sim",
        broker,
        now_fn=_now,
        lock_timeout_seconds=0.05,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(first.run, "2026-08-17", _signal())
        assert entered.wait(timeout=1.0)
        with pytest.raises(ExecutionAlreadyRunningError, match="already running"):
            second.run("2026-08-17", _signal())
        release.set()
        result = future.result(timeout=2.0)

    assert result["status"] == "completed"
    assert [call[0:2] for call in broker.calls].count(("execute", "sell")) == 1
    assert [call[0:2] for call in broker.calls].count(("execute", "buy")) == 1


def test_existing_result_rejects_different_same_day_batch(tmp_path):
    broker = FakeBroker()
    engine = QmtExecutionEngine(tmp_path, "qmt_sim", broker, now_fn=_now)
    engine.run("2026-08-17", _signal())
    different = _signal()
    different["batch_id"] = "another-batch"
    different = with_checksum(different)

    with pytest.raises(ValueError, match="batch_id"):
        engine.run("2026-08-17", different)


def test_engine_rejects_tampered_direct_signal_before_broker_query(tmp_path):
    broker = FakeBroker()
    signal = _signal()
    signal["account_alias"] = "another-account"

    with pytest.raises(ValueError, match="checksum"):
        QmtExecutionEngine(tmp_path, "qmt_sim", broker, now_fn=_now).run(
            "2026-08-17", signal
        )

    assert broker.calls == []


def test_engine_uses_real_account_after_partial_sell_for_buy_budget(tmp_path):
    broker = FakeBroker(partial_sell=True)
    result = QmtExecutionEngine(tmp_path, "qmt_sim", broker, now_fn=_now).run("2026-08-17", _signal())

    assert result["status"] == "partial"
    assert result["sell_stage"]["fills"][0]["shares"] == 50
    assert result["account_after_sell"]["holdings"][0]["shares"] == 50
    assert result["buy_stage"]["planned"][0]["shares"] == 200


def test_engine_preserves_sell_market_and_records_separate_buy_market(tmp_path):
    class MovingMarketBroker(FakeBroker):
        def __init__(self):
            super().__init__()
            self.market_calls = 0

        def market_snapshot(self, codes, exec_date):
            self.market_calls += 1
            snapshot = super().market_snapshot(codes, exec_date)
            for quote in snapshot.values():
                quote["ask1"] += self.market_calls / 100.0
            return snapshot

    broker = MovingMarketBroker()
    result = QmtExecutionEngine(tmp_path, "qmt_sim", broker, now_fn=_now).run("2026-08-17", _signal())

    assert result["market_snapshot"]["SH600002"]["ask1"] == pytest.approx(10.02)
    assert result["buy_market_snapshot"]["SH600002"]["ask1"] == pytest.approx(10.03)
    assert result["buy_stage"]["planned"][0]["reference_price"] == pytest.approx(10.03)


def test_engine_recovers_submitted_stage_without_resubmitting(tmp_path):
    broker = FakeBroker()
    signal = _signal()
    engine = QmtExecutionEngine(tmp_path, "qmt_sim", broker, now_fn=_now)
    state_file = _state_path(tmp_path, "2026-08-17", signal["batch_id"])
    original_execute = broker.execute_stage

    def crash_after_submit(stage, orders, wait_seconds):
        outcome = original_execute(stage, orders, wait_seconds)
        if stage == "sell":
            raise RuntimeError("simulated crash")
        return outcome

    broker.execute_stage = crash_after_submit
    try:
        engine.run("2026-08-17", signal)
    except RuntimeError as exc:
        assert "simulated crash" in str(exc)
    else:
        raise AssertionError("crash was not raised")
    pending_state = json_load(state_file)
    assert pending_state["phase"] == "sell_submitted"
    assert pending_state["result"]["sell_stage"]["terminal"] is False

    broker.execute_stage = original_execute
    result = engine.run("2026-08-17", signal)

    assert result["status"] == "completed"
    assert [call[0:2] for call in broker.calls].count(("execute", "sell")) == 1
    assert ("recover", "sell") in [call[0:2] for call in broker.calls]


def test_engine_recovers_submitted_stage_after_signal_expiry_and_window(tmp_path):
    broker = FakeBroker()
    signal = _signal()
    write_signal(tmp_path, signal)
    engine = QmtExecutionEngine(tmp_path, "qmt_sim", broker, now_fn=_now)
    original_execute = broker.execute_stage

    def crash_after_submit(stage, orders, wait_seconds):
        outcome = original_execute(stage, orders, wait_seconds)
        if stage == "sell":
            raise RuntimeError("simulated crash")
        return outcome

    broker.execute_stage = crash_after_submit
    with pytest.raises(RuntimeError, match="simulated crash"):
        engine.run("2026-08-17")

    broker.execute_stage = original_execute
    late = lambda: datetime(2026, 8, 17, 10, 0, 0, tzinfo=TZ)
    result = QmtExecutionEngine(tmp_path, "qmt_sim", broker, now_fn=late).run("2026-08-17")

    assert result["status"] == "completed"
    assert [call[0:2] for call in broker.calls].count(("execute", "sell")) == 1
    assert ("recover", "sell") in [call[0:2] for call in broker.calls]


def test_engine_continues_sell_closed_after_signal_expiry_without_reselling(tmp_path):
    class RefreshCrashBroker(FakeBroker):
        def __init__(self):
            super().__init__()
            self.market_calls = 0
            self.fail_refresh = True

        def market_snapshot(self, codes, exec_date):
            self.market_calls += 1
            if self.fail_refresh and self.market_calls == 2:
                raise RuntimeError("buy quote refresh crashed")
            return super().market_snapshot(codes, exec_date)

    broker = RefreshCrashBroker()
    signal = _signal()
    write_signal(tmp_path, signal)
    engine = QmtExecutionEngine(tmp_path, "qmt_sim", broker, now_fn=_now)
    state_file = _state_path(tmp_path, "2026-08-17", signal["batch_id"])

    with pytest.raises(RuntimeError, match="buy quote refresh crashed"):
        engine.run("2026-08-17")
    assert json_load(state_file)["phase"] == "sell_closed"

    broker.fail_refresh = False
    late = lambda: datetime(2026, 8, 17, 10, 0, 0, tzinfo=TZ)
    result = QmtExecutionEngine(tmp_path, "qmt_sim", broker, now_fn=late).run("2026-08-17")

    assert result["status"] == "completed"
    assert [call[0:2] for call in broker.calls].count(("execute", "sell")) == 1
    assert [call[0:2] for call in broker.calls].count(("execute", "buy")) == 1


def test_nonterminal_outcome_keeps_submitted_state_and_never_starts_buy(tmp_path):
    broker = FakeBroker()
    signal = _signal()
    engine = QmtExecutionEngine(tmp_path, "qmt_sim", broker, now_fn=_now)
    state_file = _state_path(tmp_path, "2026-08-17", signal["batch_id"])
    original_execute = broker.execute_stage

    def pending_execute(stage, orders, wait_seconds):
        outcome = original_execute(stage, orders, wait_seconds)
        outcome["terminal"] = False
        return outcome

    broker.execute_stage = pending_execute
    with pytest.raises(RecoveryRequiredError, match="not proven terminal"):
        engine.run("2026-08-17", signal)

    assert json_load(state_file)["phase"] == "sell_submitted"
    assert not result_path(tmp_path, "2026-08-17").exists()
    assert [call[1] for call in broker.calls] == ["sell"]

    with pytest.raises(RecoveryRequiredError, match="not proven terminal"):
        engine.run("2026-08-17", signal)

    assert [call[0:2] for call in broker.calls] == [("execute", "sell"), ("recover", "sell")]
    assert not result_path(tmp_path, "2026-08-17").exists()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda outcome: outcome["broker_orders"].append(
                {"order_id": "unknown-order", "status": "terminal"}
            ),
            "unknown order_id",
        ),
        (
            lambda outcome: outcome["fills"].append(dict(outcome["fills"][0])),
            "fill ids",
        ),
        (
            lambda outcome: outcome["fills"][0].update({"shares": 101}),
            "exceed planned shares",
        ),
    ],
)
def test_stage_outcome_rejects_unrelated_duplicate_or_excess_facts(mutate, message):
    planned = [
        {
            "order_id": "batch:sell:001",
            "code": "SH600001",
            "side": "sell",
            "shares": 100,
        }
    ]
    outcome = {
        "terminal": True,
        "broker_orders": [{"order_id": "batch:sell:001", "status": "terminal"}],
        "fills": [
            {
                "fill_id": "fill-1",
                "order_id": "batch:sell:001",
                "code": "SH600001",
                "side": "sell",
                "shares": 100,
                "price": 10.0,
            }
        ],
        "cancelled": [],
        "errors": [],
    }
    mutate(outcome)

    with pytest.raises(QmtExecutionError, match=message):
        _normalize_outcome(outcome, "sell", planned)


def test_engine_aborts_when_submitted_stage_cannot_be_recovered(tmp_path):
    broker = FakeBroker()
    signal = _signal()
    engine = QmtExecutionEngine(tmp_path, "qmt_sim", broker, now_fn=_now)
    state_file = _state_path(tmp_path, "2026-08-17", signal["batch_id"])
    # Create a normal state, then force the crash window without broker evidence.
    original = broker.execute_stage

    def crash_before_evidence(stage, orders, wait_seconds):
        raise RuntimeError("unknown submission outcome")

    broker.execute_stage = crash_before_evidence
    try:
        engine.run("2026-08-17", signal)
    except RuntimeError:
        pass
    assert json_load(state_file)["phase"] == "sell_submitted"
    broker.execute_stage = original

    result = engine.run("2026-08-17", signal)

    assert result["status"] == "aborted"
    assert result["reason"] == "manual_reconciliation_required"
    assert [call[0:2] for call in broker.calls] == [("recover", "sell")]


def test_eod_snapshot_uses_broker_total_asset_and_is_idempotent(tmp_path):
    broker = FakeBroker()

    first = write_eod_from_broker(
        tmp_path,
        "2026-08-17",
        "batch",
        broker,
        now=datetime(2026, 8, 17, 15, 0, tzinfo=TZ),
    )
    broker.cash = 1.0
    second = write_eod_from_broker(
        tmp_path,
        "2026-08-17",
        "batch",
        broker,
        now=datetime(2026, 8, 17, 15, 1, tzinfo=TZ),
    )

    assert first == second
    assert first["total_asset"] == 3_200.0
    assert first["source"] == "broker_qmt"


def test_eod_snapshot_rejects_preclose_account_value(tmp_path):
    with pytest.raises(QmtExecutionError, match="before 15:00"):
        write_eod_from_broker(
            tmp_path,
            "2026-08-17",
            "batch",
            FakeBroker(),
            now=datetime(2026, 8, 17, 14, 55, tzinfo=TZ),
        )

    assert not eod_snapshot_path(tmp_path, "2026-08-17").exists()


@pytest.mark.parametrize(
    "account_patch",
    [
        {"source": "local_estimate"},
        {"cash": float("nan")},
        {"market_value": -1.0},
        {"frozen_cash": None},
    ],
)
def test_eod_snapshot_rejects_untrusted_or_invalid_broker_facts(tmp_path, account_patch):
    class InvalidBroker(FakeBroker):
        def account_snapshot(self):
            account = super().account_snapshot()
            account.update(account_patch)
            return account

    with pytest.raises(QmtExecutionError, match="EOD"):
        write_eod_from_broker(
            tmp_path,
            "2026-08-17",
            "batch",
            InvalidBroker(),
            now=datetime(2026, 8, 17, 15, 0, tzinfo=TZ),
        )


def test_first_release_rejects_simulation_config(tmp_path):
    config = tmp_path / "qmt_config.json"
    config.write_text(
        """{
          "repo_root": "D:/code/qlib",
          "account_alias": "qmt_sim",
          "account_id": "simulation-account",
          "account_type": "STOCK",
          "mode": "simulation"
        }""",
        encoding="utf-8",
    )

    with pytest.raises(QmtExecutionError, match="read_only mode only"):
        load_local_config(config)


def test_first_release_rejects_configurable_unreviewed_bridge():
    with pytest.raises(QmtExecutionError, match="only permits"):
        load_broker_bridge(
            {
                "bridge_module": "untrusted.bridge",
                "bridge_class": "SideEffectBridge",
            },
            object(),
        )


def json_load(path):
    import json

    return json.loads(path.read_text(encoding="utf-8"))

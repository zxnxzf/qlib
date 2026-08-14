import pandas as pd

from my.quant import config as C
from my.quant import ledger, nightly
from my.quant.execution import Receipt
from my.quant.signal_package import build_signal_package, save_signal_package
from my.quant.trade_planner import (
    AccountSnapshot,
    HoldingSnapshot,
    MarketSnapshot,
    QuoteSnapshot,
)


PARAMS = {
    "topk": 2,
    "candidate_limit": 100,
    "n_drop": 1,
    "hold_thresh": 1,
    "risk_degree": 0.95,
    "lot": 100,
    "open_cost": 0.0005,
    "close_cost": 0.0015,
    "min_cost": 5.0,
    "max_slippage": 0.003,
}
PROVENANCE = {
    "source_type": "published_model",
    "strategy_id": "lgb_alpha158_gate905_v1",
    "release_id": "2026Q3",
    "model_sha256": "1" * 64,
    "config_sha256": "2" * 64,
    "runtime_code_sha256": "3" * 64,
    "source_git_commit": "4" * 40,
}


def _quote(code, price=10.0):
    return QuoteSnapshot(
        code=code,
        timestamp="2026-08-05T09:30:05",
        bid1=price,
        ask1=price,
        last=price,
        high_limit=price * 1.1,
        low_limit=price * 0.9,
        buyable=True,
        sellable=True,
        status="normal",
    )


def _market(*codes):
    return MarketSnapshot("2026-08-05", {code: _quote(code) for code in codes})


def _seed_batch(tmp_path, *, holdings, cash, topk=2, phase="signal_ready"):
    params = {**PARAMS, "topk": topk}
    scores = pd.Series(
        {"SH600001": -2.0, "SH600002": 1.0, "SZ000001": 3.0},
        name="score",
    )
    package = build_signal_package(
        scores=scores,
        signal_date="2026-08-04",
        exec_date="2026-08-05",
        gate_on=True,
        holding_codes=holdings,
        params=params,
        batch_id="2026-08-05-v1",
        reference_closes={code: 10.0 for code in scores.index},
        provenance=PROVENANCE,
    )
    save_signal_package(package, tmp_path)
    ledger.save_state(
        {
            "cash": cash,
            "holdings": holdings,
            "last_prices": {},
            "last_settled": "2026-08-04",
            "pending_exec_date": "2026-08-05",
            "pending_batch_id": package.batch_id,
            "phase": phase,
        }
    )
    if phase == "sell_closed":
        ledger.save_receipts("2026-08-05", [], stage="sell")
    return package


class RecordingAdapter:
    def __init__(self, tmp_path, responses):
        self.tmp_path = tmp_path
        self.responses = list(responses)
        self.calls = []

    def submit_and_wait(self, orders, exec_date, account, market, wait_seconds):
        side = orders[0].side if orders else "empty"
        self.calls.append((side, tuple(order.code for order in orders), account.cash, set(account.holdings)))
        if side == "buy":
            assert (self.tmp_path / "receipts" / f"{exec_date}_sell.csv").exists()
            assert ledger.load_state()["phase"] == "buy_submitted"
        response = self.responses.pop(0)
        return response(orders) if callable(response) else response


def test_execute_persists_sell_receipts_before_planning_buys(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "STATE_DIR", tmp_path)
    _seed_batch(tmp_path, holdings={"SH600001": 100, "SH600002": 100}, cash=0.0)
    adapter = RecordingAdapter(
        tmp_path,
        [
            lambda orders: [Receipt(orders[0].code, "sell", 100, 10.0, 5.0, "filled")],
            lambda orders: [Receipt(orders[0].code, "buy", orders[0].shares, 9.0, 5.0, "filled")],
        ],
    )

    summary = nightly.execute(
        "2026-08-05",
        adapter=adapter,
        market=MarketSnapshot(
            "2026-08-05",
            {
                "SH600001": _quote("SH600001"),
                "SH600002": _quote("SH600002"),
                "SZ000001": _quote("SZ000001", price=9.0),
            },
        ),
    )

    assert [call[0] for call in adapter.calls] == ["sell", "buy"]
    assert summary["phase"] == "completed"
    assert ledger.load_state()["holdings"] == {"SH600002": 100, "SZ000001": 100}


def test_blocked_sell_does_not_release_a_buy_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "STATE_DIR", tmp_path)
    _seed_batch(tmp_path, holdings={"SH600001": 100}, cash=0.0, topk=1)
    adapter = RecordingAdapter(
        tmp_path,
        [[Receipt("SH600001", "sell", 0, 0.0, 0.0, "blocked_limit")]],
    )

    summary = nightly.execute(
        "2026-08-05",
        adapter=adapter,
        market=_market("SH600001", "SH600002", "SZ000001"),
    )

    assert [call[0] for call in adapter.calls] == ["sell"]
    assert summary["phase"] == "partial"
    assert ledger.load_state()["holdings"] == {"SH600001": 100}


def test_timed_out_buy_remains_cash(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "STATE_DIR", tmp_path)
    _seed_batch(tmp_path, holdings={}, cash=10_000.0, topk=1)
    adapter = RecordingAdapter(
        tmp_path,
        [lambda orders: [Receipt(orders[0].code, "buy", 0, 0.0, 0.0, "timed_out")]],
    )

    summary = nightly.execute(
        "2026-08-05",
        adapter=adapter,
        market=_market("SH600001", "SH600002", "SZ000001"),
    )

    state = ledger.load_state()
    assert [call[0] for call in adapter.calls] == ["buy"]
    assert summary["phase"] == "partial"
    assert state["cash"] == 10_000.0
    assert state["holdings"] == {}


def test_duplicate_completed_batch_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "STATE_DIR", tmp_path)
    _seed_batch(tmp_path, holdings={}, cash=10_000.0, topk=1)
    adapter = RecordingAdapter(
        tmp_path,
        [lambda orders: [Receipt(orders[0].code, "buy", orders[0].shares, 10.0, 5.0, "filled")]],
    )
    market = _market("SH600001", "SH600002", "SZ000001")

    nightly.execute("2026-08-05", adapter=adapter, market=market)
    repeated = nightly.execute("2026-08-05", adapter=adapter, market=market)

    assert repeated == {"date": "2026-08-05", "phase": "completed", "noop": True}
    assert len(adapter.calls) == 1


def test_restart_from_sell_closed_does_not_submit_sells_again(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "STATE_DIR", tmp_path)
    _seed_batch(tmp_path, holdings={"SH600002": 100}, cash=995.0, phase="sell_closed")
    adapter = RecordingAdapter(
        tmp_path,
        [lambda orders: [Receipt(orders[0].code, "buy", 100, 9.0, 5.0, "filled")]],
    )

    nightly.execute(
        "2026-08-05",
        adapter=adapter,
        market=MarketSnapshot(
            "2026-08-05",
            {
                "SH600001": _quote("SH600001"),
                "SH600002": _quote("SH600002"),
                "SZ000001": _quote("SZ000001", price=9.0),
            },
        ),
    )

    assert [call[0] for call in adapter.calls] == ["buy"]

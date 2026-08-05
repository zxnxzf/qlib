from datetime import datetime

import pandas as pd

from my.quant import config as C
from my.quant import data, gate, ledger, nightly, signal_
from my.quant.execution import ShadowExecutor
from my.quant.portfolio import Order
from my.scripts import shadow_run


def test_gate_uses_maintained_csi500_index():
    assert C.GATE_INDEX == "SH000905"


def test_gate_fallback_uses_configured_index(monkeypatch):
    requested = []

    class Response:
        def read(self):
            return b'v_sh000905="1~name~code~6123.45~tail";'

    def fake_urlopen(url, timeout):
        requested.append(url)
        return Response()

    monkeypatch.setattr(data.urllib.request, "urlopen", fake_urlopen)

    assert data.index_close_fallback() == 6123.45
    assert requested == ["https://qt.gtimg.cn/q=sh000905"]


def test_gate_note_names_csi500(monkeypatch):
    closes = pd.Series(
        range(100, 130),
        index=pd.date_range("2026-06-01", periods=30, freq="B"),
        dtype=float,
    )
    monkeypatch.setattr(data, "index_closes", lambda *_args, **_kwargs: closes)

    _gate_on, note = gate.gate_for_next_day("2026-07-10")

    assert "中证500" in note
    assert "全指" not in note


def test_expected_signal_date_uses_today_after_close(monkeypatch):
    monkeypatch.setattr(
        data,
        "future_calendar",
        lambda: ["2026-08-03", "2026-08-04", "2026-08-05"],
    )

    assert data.expected_signal_date(datetime(2026, 8, 4, 20, 30)) == "2026-08-04"


def test_expected_signal_date_uses_previous_day_before_close(monkeypatch):
    monkeypatch.setattr(
        data,
        "future_calendar",
        lambda: ["2026-08-03", "2026-08-04", "2026-08-05"],
    )

    assert data.expected_signal_date(datetime(2026, 8, 5, 6, 30)) == "2026-08-04"


def test_shadow_executor_rejects_buy_when_cash_is_insufficient(monkeypatch):
    bars = pd.DataFrame(
        {"open": [10.0], "close": [10.0], "volume": [1000.0], "factor": [1.0], "prev_close": [10.0]},
        index=["SH600000"],
    )
    monkeypatch.setattr(data, "day_bars", lambda *_args, **_kwargs: bars)
    order = Order("SH600000", "buy", 100, 10.0, "test")

    receipts = ShadowExecutor().settle([order], "2026-08-04", cash=500.0)

    assert receipts[0].shares == 0
    assert receipts[0].status == "insufficient_cash"


def test_shadow_executor_rejects_sell_larger_than_position(monkeypatch):
    bars = pd.DataFrame(
        {"open": [10.0], "close": [10.0], "volume": [1000.0], "factor": [1.0], "prev_close": [10.0]},
        index=["SH600000"],
    )
    monkeypatch.setattr(data, "day_bars", lambda *_args, **_kwargs: bars)
    order = Order("SH600000", "sell", 200, 10.0, "test")

    receipts = ShadowExecutor().settle(
        [order], "2026-08-04", cash=500.0, holdings={"SH600000": 100}
    )

    assert receipts[0].shares == 0
    assert receipts[0].status == "insufficient_position"


def test_market_snapshot_keeps_stock_tradable_when_previous_close_is_missing(monkeypatch):
    bars = pd.DataFrame(
        {
            "open": [10.0],
            "close": [10.0],
            "volume": [1_000.0],
            "factor": [1.0],
            "prev_close": [float("nan")],
        },
        index=["SH600811"],
    )
    monkeypatch.setattr(data, "day_bars", lambda *_args, **_kwargs: bars)

    quote = nightly._market_snapshot("2025-03-18").quotes["SH600811"]

    assert quote.status == "normal"
    assert quote.buyable is True
    assert quote.sellable is True


def test_mark_to_market_persists_last_price_across_processes(monkeypatch):
    priced = pd.DataFrame(
        {"close": [10.0], "factor": [1.0], "prev_close": [9.9]},
        index=["SH600000"],
    )
    suspended = pd.DataFrame(
        {"close": [float("nan")], "factor": [1.0], "prev_close": [10.0]},
        index=["SH600000"],
    )
    monkeypatch.setattr(data, "day_bars", lambda *_args, **_kwargs: priced)

    nav, last_prices = nightly._mark_to_market({"SH600000": 100}, 1000.0, "2026-08-03", {})

    monkeypatch.setattr(data, "day_bars", lambda *_args, **_kwargs: suspended)
    restarted_nav, restarted_prices = nightly._mark_to_market(
        {"SH600000": 100}, 1000.0, "2026-08-04", last_prices
    )
    assert nav == 2000.0
    assert restarted_nav == 2000.0
    assert restarted_prices == {"SH600000": 10.0}


def test_signal_reference_closes_use_last_known_price_for_suspended_top100(monkeypatch):
    bars = pd.DataFrame(
        {
            "close": [10.0, float("nan")],
            "factor": [1.0, 0.5],
            "prev_close": [9.9, 5.0],
        },
        index=["SH600001", "SZ300307"],
    )
    monkeypatch.setattr(data, "day_bars", lambda *_args, **_kwargs: bars)
    monkeypatch.setattr(
        data,
        "raw_closes_asof",
        lambda codes, date: {"SZ300307": 9.8} if list(codes) == ["SZ300307"] else {},
        raising=False,
    )

    closes = nightly._raw_closes("2026-08-04", required_codes=["SZ300307"])

    assert closes["SH600001"] == 10.0
    assert closes["SZ300307"] == 9.8


def test_new_ledger_state_persists_last_prices(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "STATE_DIR", tmp_path)

    state = ledger.load_state()

    assert state["last_prices"] == {}


def test_nightly_stalls_instead_of_processing_stale_data(monkeypatch):
    events = []
    monkeypatch.setattr(data, "update_data", lambda: True)
    monkeypatch.setattr(data, "expected_signal_date", lambda: "2026-08-04")
    monkeypatch.setattr(data, "latest_data_date", lambda: "2026-08-03")
    monkeypatch.setattr(ledger, "append_log", events.append)

    def fail_if_state_is_loaded():
        raise AssertionError("stale data must stop before the account is touched")

    monkeypatch.setattr(ledger, "load_state", fail_if_state_is_loaded)

    summary = nightly.run_evening()

    assert summary == {"date": "2026-08-04", "stall": True}
    assert events == ["STALL 2026-08-04 数据未覆盖（本地最新 2026-08-03）"]


def test_nightly_retry_is_idempotent_after_date_was_processed(monkeypatch):
    state = {
        "cash": 100_000.0,
        "holdings": {},
        "last_prices": {},
        "last_settled": "2026-08-04",
        "pending_exec_date": "2026-08-05",
    }
    monkeypatch.setattr(data, "latest_data_date", lambda: "2026-08-04")
    monkeypatch.setattr(ledger, "load_state", lambda: state)

    def fail_if_market_is_touched(*_args, **_kwargs):
        raise AssertionError("an idempotent retry must not price or trade twice")

    monkeypatch.setattr(data, "day_bars", fail_if_market_is_touched)

    summary = nightly.run_evening(asof="2026-08-04", skip_update=True)

    assert summary == {"date": "2026-08-04", "noop": True}


def test_nightly_rejects_date_older_than_last_processed_date(monkeypatch):
    state = {
        "cash": 100_000.0,
        "holdings": {},
        "last_prices": {},
        "last_settled": "2026-08-05",
        "pending_exec_date": "2026-08-06",
    }
    monkeypatch.setattr(data, "latest_data_date", lambda: "2026-08-05")
    monkeypatch.setattr(ledger, "load_state", lambda: state)

    def fail_if_market_is_touched(*_args, **_kwargs):
        raise AssertionError("a past signal date must not be processed after a newer date")

    monkeypatch.setattr(data, "day_bars", fail_if_market_is_touched)

    summary = nightly.run_evening(asof="2026-08-04", skip_update=True)

    assert summary == {"date": "2026-08-04", "noop": True}


def test_backfill_uses_isolated_ledger_directory(tmp_path, monkeypatch):
    live_state = tmp_path / "live"
    monkeypatch.setattr(C, "STATE_DIR", live_state)

    selected = shadow_run._configure_backfill_state(
        "2026-07-20", "2026-08-01", run_id="test-run"
    )

    assert selected == live_state / "backfills" / "2026-07-20_2026-08-01_test-run"
    assert C.STATE_DIR == selected


def test_retry_sell_is_not_duplicated_when_strategy_sells_same_stock():
    orders = [
        Order("SH600000", "sell", 100, 10.0, "dropout"),
        Order("SZ000001", "buy", 100, 20.0, "topk_entry"),
    ]
    retries = [
        {"code": "SH600000", "side": "sell", "shares": 100, "ref_price": 9.8, "reason": "dropout"}
    ]

    merged = nightly._merge_retry_sells(orders, retries)

    sells = [o for o in merged if o.side == "sell" and o.code == "SH600000"]
    assert len(sells) == 1


def test_retry_sell_is_kept_when_strategy_does_not_repeat_it():
    orders = [Order("SZ000001", "buy", 100, 20.0, "topk_entry")]
    retries = [
        {"code": "SH600000", "side": "sell", "shares": 100, "ref_price": 9.8, "reason": "dropout"}
    ]

    merged = nightly._merge_retry_sells(orders, retries)

    assert [(o.code, o.side, o.reason) for o in merged] == [
        ("SH600000", "sell", "dropout_retry"),
        ("SZ000001", "buy", "topk_entry"),
    ]


def test_two_day_shadow_flow_persists_orders_receipts_and_state(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "STATE_DIR", tmp_path)
    monkeypatch.setattr(C, "TOPK", 2)
    monkeypatch.setattr(C, "N_DROP", 1)
    monkeypatch.setattr(data, "latest_data_date", lambda: "2026-08-05")
    monkeypatch.setattr(
        data,
        "next_trade_date",
        lambda date: {"2026-08-03": "2026-08-04", "2026-08-04": "2026-08-05"}.get(date),
    )
    bars = pd.DataFrame(
        {
            "open": [10.0, 10.0, 10.0],
            "close": [10.0, 10.0, 10.0],
            "volume": [1000.0, 1000.0, 1000.0],
            "factor": [1.0, 1.0, 1.0],
            "prev_close": [10.0, 10.0, 10.0],
        },
        index=["SH600000", "SZ000001", "SZ000002"],
    )
    monkeypatch.setattr(data, "day_bars", lambda *_args, **_kwargs: bars)
    monkeypatch.setattr(gate, "gate_for_next_day", lambda _date: (True, "test gate on"))

    def scores_for(date, log=print):
        values = {
            "2026-08-03": [3.0, 2.0, 1.0],
            "2026-08-04": [1.0, 2.0, 3.0],
        }[date]
        return pd.Series(values, index=bars.index, name="score")

    monkeypatch.setattr(signal_, "scores_for", scores_for)

    first = nightly.run_evening(asof="2026-08-03", skip_update=True, log=lambda _msg: None)
    second = nightly.run_evening(asof="2026-08-04", skip_update=True, log=lambda _msg: None)
    state = ledger.load_state()

    assert first["prepared"] == "2026-08-04"
    assert second["settled"] == "2/2"
    assert state["pending_exec_date"] == "2026-08-05"
    assert state["holdings"] == {"SH600000": 4700, "SZ000001": 4700}
    assert (tmp_path / "signals" / "2026-08-04.json").exists()
    assert (tmp_path / "orders" / "2026-08-04_buy.csv").exists()
    assert (tmp_path / "receipts" / "2026-08-04_buy.csv").exists()
    assert (tmp_path / "signals" / "2026-08-05.json").exists()
    assert not (tmp_path / "orders" / "2026-08-05_buy.csv").exists()


def test_shadow_run_prepare_command(monkeypatch):
    calls = []
    monkeypatch.setattr(shadow_run.sys, "argv", ["shadow_run.py", "prepare", "2026-08-04"])
    monkeypatch.setattr(nightly, "prepare", lambda asof=None: calls.append(asof))

    assert shadow_run.main() == 0
    assert calls == ["2026-08-04"]


def test_shadow_run_execute_command(monkeypatch):
    calls = []
    monkeypatch.setattr(shadow_run.sys, "argv", ["shadow_run.py", "execute", "2026-08-05"])
    monkeypatch.setattr(nightly, "execute", lambda exec_date: calls.append(exec_date))

    assert shadow_run.main() == 0
    assert calls == ["2026-08-05"]

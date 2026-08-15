from datetime import datetime, timedelta, timezone

import pytest

from my.qmt.deploy import build_entry, build_probe_entry
from my.qmt.guojin_bridge import GuojinQmtBridge
from my.qmt.qmt_strategy import QmtExecutionError


TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 17, 9, 30, 10, tzinfo=TZ)


class Account:
    m_dAvailable = 5_000.0
    m_dBalance = 6_000.0
    m_dInstrumentValue = 1_000.0


class Position:
    m_strInstrumentID = "600001.SH"
    m_nVolume = 100
    m_nCanUseVolume = 100
    m_dLastPrice = 10.0


class Context:
    def get_full_tick(self, codes):
        return {
            code: {
                "lastPrice": 10.0,
                "bidPrice": [9.99],
                "askPrice": [10.01],
                "UpStopPrice": 11.0,
                "DownStopPrice": 9.0,
                "timestamp": "2026-08-17T09:30:05+08:00",
            }
            for code in codes
        }


def _query(_account, _account_type, kind):
    return [Account()] if kind == "account" else [Position()]


def test_read_only_bridge_normalizes_broker_account_and_quotes():
    bridge = GuojinQmtBridge(
        Context(),
        {"account_id": "secret", "account_type": "STOCK"},
        {"get_trade_detail_data": _query},
        now_fn=lambda: NOW,
    )

    account = bridge.account_snapshot()
    market = bridge.market_snapshot(["SH600001"], "2026-08-17")

    assert account["cash"] == 5_000.0
    assert account["total_asset"] is None
    assert account["holdings"][0]["code"] == "SH600001"
    assert "secret" not in repr(account)
    assert market["SH600001"]["buyable"] is True
    assert market["SH600001"]["sellable"] is True


def test_build_entry_points_qmt_to_repository_without_copying_runtime_data(tmp_path):
    repo = tmp_path / "qlib"
    repo.mkdir()
    output = tmp_path / "qmt" / "qlib_qmt_entry.py"

    built = build_entry(repo, output)
    text = built.read_text(encoding="utf-8")

    assert str(repo.resolve()) in text
    assert str((repo / "my" / "runtime" / "qmt_config.json").resolve()) in text
    assert "QMT_API_NAMESPACE = globals()" in text
    assert "sys.version_info < (3, 8)" in text
    assert "config.repo_root does not match" in text
    assert "[QMT][DISABLED]" in text


def test_build_probe_entry_injects_qmt_namespace_and_writes_only_under_runtime(tmp_path):
    repo = tmp_path / "qlib"
    repo.mkdir()
    output = tmp_path / "qmt" / "qlib_qmt_probe.py"

    built = build_probe_entry(repo, output)
    text = built.read_text(encoding="utf-8")

    assert str(repo.resolve()) in text
    assert str((repo / "my" / "runtime" / "qmt_state" / "qmt_probe.json").resolve()) in text
    assert "QMT_API_NAMESPACE = globals()" in text
    assert "qmt_probe as _impl" in text


def test_quote_timestamp_requires_execution_day():
    assert GuojinQmtBridge._quote_timestamp("20260817093005", "2026-08-17") == (
        "2026-08-17T09:30:05+08:00"
    )
    assert GuojinQmtBridge._quote_timestamp("20260816093005", "2026-08-17") == (
        "2026-08-16T09:30:05+08:00"
    )
    assert GuojinQmtBridge._quote_timestamp("prefix-20260817093005", "2026-08-17") == ""
    assert GuojinQmtBridge._quote_timestamp("2026-08-17 09:30:05", "2026-08-17") == ""


def test_position_mapping_response_uses_mapping_code():
    def query(_account, _account_type, kind):
        if kind == "account":
            return [Account()]
        return {
            "600001.SH": {
                "m_nVolume": 100,
                "m_nCanUseVolume": 100,
                "m_dLastPrice": 10.0,
            }
        }

    bridge = GuojinQmtBridge(
        Context(),
        {"account_id": "secret", "account_type": "STOCK"},
        {"get_trade_detail_data": query},
        now_fn=lambda: NOW,
    )

    assert bridge.account_snapshot()["holdings"][0]["code"] == "SH600001"


def test_nonempty_unparseable_position_response_fails_closed():
    def query(_account, _account_type, kind):
        if kind == "account":
            return [Account()]
        return {"600001.SH": {"unknown_volume": 100}}

    bridge = GuojinQmtBridge(
        Context(),
        {"account_id": "secret", "account_type": "STOCK"},
        {"get_trade_detail_data": query},
        now_fn=lambda: NOW,
    )

    with pytest.raises(QmtExecutionError, match="invalid code or shares"):
        bridge.account_snapshot()


def test_empty_position_query_with_positive_market_value_fails_closed():
    def query(_account, _account_type, kind):
        return [Account()] if kind == "account" else []

    bridge = GuojinQmtBridge(
        Context(),
        {"account_id": "secret", "account_type": "STOCK"},
        {"get_trade_detail_data": query},
        now_fn=lambda: NOW,
    )

    with pytest.raises(QmtExecutionError, match="market value is positive"):
        bridge.account_snapshot()


@pytest.mark.parametrize(
    "positions",
    [
        [
            Position(),
            {"m_strInstrumentID": "600002.SH", "m_nVolume": "bad", "m_nCanUseVolume": 0},
        ],
        [
            {"m_strInstrumentID": "600001.SH", "m_nVolume": 100, "m_nCanUseVolume": 200},
        ],
        [Position(), Position()],
    ],
)
def test_any_incomplete_or_inconsistent_position_fails_entire_snapshot(positions):
    def query(_account, _account_type, kind):
        return [Account()] if kind == "account" else positions

    bridge = GuojinQmtBridge(
        Context(),
        {"account_id": "secret", "account_type": "STOCK"},
        {"get_trade_detail_data": query},
        now_fn=lambda: NOW,
    )

    with pytest.raises(QmtExecutionError):
        bridge.account_snapshot()


@pytest.mark.parametrize("invalid", ["not-a-number", float("nan"), float("inf"), -1.0])
def test_invalid_or_unknown_total_asset_is_none(invalid):
    class InvalidTotalAccount(Account):
        m_dTotalAsset = invalid

    def query(_account, _account_type, kind):
        return [InvalidTotalAccount()] if kind == "account" else [Position()]

    bridge = GuojinQmtBridge(
        Context(),
        {"account_id": "secret", "account_type": "STOCK"},
        {"get_trade_detail_data": query},
        now_fn=lambda: NOW,
    )

    assert bridge.account_snapshot()["total_asset"] is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("m_dAvailable", float("nan"), "available-cash"),
        ("m_dInstrumentValue", float("inf"), "market-value"),
    ],
)
def test_invalid_cash_or_market_value_fails_closed(field, value, message):
    account = Account()
    setattr(account, field, value)

    def query(_account, _account_type, kind):
        return [account] if kind == "account" else [Position()]

    bridge = GuojinQmtBridge(
        Context(),
        {"account_id": "secret", "account_type": "STOCK"},
        {"get_trade_detail_data": query},
        now_fn=lambda: NOW,
    )

    with pytest.raises(QmtExecutionError, match=message):
        bridge.account_snapshot()


def test_stale_same_day_quote_is_retained_but_not_tradable():
    class StaleContext(Context):
        def get_full_tick(self, codes):
            result = super().get_full_tick(codes)
            for tick in result.values():
                tick["timestamp"] = "2026-08-17T09:29:00+08:00"
            return result

    bridge = GuojinQmtBridge(
        StaleContext(),
        {"account_id": "secret", "account_type": "STOCK"},
        {"get_trade_detail_data": _query},
        now_fn=lambda: NOW,
    )

    quote = bridge.market_snapshot(["SH600001"], "2026-08-17")["SH600001"]
    assert quote["status"] == "stale_timestamp"
    assert quote["buyable"] is False
    assert quote["sellable"] is False


def test_missing_requested_quote_is_retained_as_untradable_record():
    class PartialContext(Context):
        def get_full_tick(self, codes):
            return super().get_full_tick(codes[:1])

    bridge = GuojinQmtBridge(
        PartialContext(),
        {"account_id": "secret", "account_type": "STOCK"},
        {"get_trade_detail_data": _query},
        now_fn=lambda: NOW,
    )

    market = bridge.market_snapshot(["SH600001", "SZ000001"], "2026-08-17")

    assert set(market) == {"SH600001", "SZ000001"}
    assert market["SZ000001"]["status"] == "missing_quote"
    assert market["SZ000001"]["buyable"] is False
    assert market["SZ000001"]["sellable"] is False

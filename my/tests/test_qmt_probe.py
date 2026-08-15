import json

from my.qmt import qmt_probe


def _leaf_values(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _leaf_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _leaf_values(child)
    else:
        yield value


class Account:
    def __init__(self):
        self.m_dAvailable = 80000.0
        self.m_dFrozenCash = 0.0
        self.m_dStockValue = 20000.0
        self.m_dTotalAsset = 100000.0
        self.m_strAccountID = "sensitive-account"


class Position:
    def __init__(self):
        self.m_strInstrumentID = "000001.SZ"
        self.m_nVolume = 1000
        self.m_nCanUseVolume = 1000
        self.m_dOpenPrice = 10.0
        self.m_dLastPrice = 10.5
        self.m_dMarketValue = 10500.0


class Order:
    def __init__(self):
        self.m_strOrderSysID = "order-1"
        self.m_strRemark = "probe"
        self.m_nOrderStatus = 56
        self.m_nOrderVolume = 100
        self.m_nTradedVolume = 100
        self.m_dLimitPrice = 10.51


class Deal:
    def __init__(self):
        self.m_strTradeID = "deal-1"
        self.m_strOrderSysID = "order-1"
        self.m_strRemark = "probe"
        self.m_nVolume = 100
        self.m_dPrice = 10.51
        self.m_dCommission = 5.0


class RealtimeContext:
    def __init__(self, is_last=True):
        self.accid = "sensitive-account"
        self.barpos = 7
        self._is_last = is_last
        self.quote_calls = 0

    def is_last_bar(self):
        return self._is_last

    def get_bar_timetag(self, barpos):
        return 20260817093000 + barpos

    def get_full_tick(self, symbols):
        self.quote_calls += 1
        return {
            symbol: {
                "lastPrice": 10.5,
                "bidPrice": [10.49],
                "askPrice": [10.51],
                "UpStopPrice": 11.55,
                "DownStopPrice": 9.45,
                "timestamp": "2026-08-17T09:30:05+08:00",
            }
            for symbol in symbols
        }


def _successful_namespace(call_log=None, include_history=False):
    def get_trade_detail_data(account_id, account_type, kind):
        if call_log is not None:
            call_log.append((account_id, account_type, kind))
        if kind == "account":
            return [Account()]
        if kind == "position":
            return [Position()]
        if kind == "order":
            return [Order()] if include_history else []
        if kind == "deal":
            return [Deal()] if include_history else []
        raise AssertionError(kind)

    return {"get_trade_detail_data": get_trade_detail_data}


def test_run_probe_writes_atomic_read_only_report(tmp_path, monkeypatch):
    output = tmp_path / "runtime" / "qmt_probe.json"
    monkeypatch.setattr(qmt_probe, "PROBE_ACCOUNT_ID", "")
    context = RealtimeContext()
    calls = []

    report = qmt_probe.run_probe(context, output_path=str(output), namespace=_successful_namespace(calls))

    restored = json.loads(output.read_text(encoding="utf-8"))
    rendered = output.read_text(encoding="utf-8")
    assert restored == report
    assert report["ready"] is True
    assert report["ready_for_trading"] is False
    assert report["mode"]["read_only"] is True
    assert report["mode"]["order_probe"]["order_submitted"] is False
    assert report["observed_fields"]["account_metrics"]["total_asset"] == "m_dTotalAsset"
    assert report["observed_fields"]["position"]["available_volume"] == "m_nCanUseVolume"
    assert report["observed_fields"]["quote"]["ask"] == "askPrice"
    assert [item[2] for item in calls] == ["account", "position", "order", "deal"]
    assert report["capabilities"]["order_query"]["status"] == "empty"
    assert report["capabilities"]["order_query"]["object_count"] == 0
    assert report["capabilities"]["deal_query"]["status"] == "empty"
    assert report["capabilities"]["deal_query"]["object_count"] == 0
    assert report["ready"] is True
    assert report["capabilities"]["account_query"]["objects"][0]["fields"]["m_strAccountID"] == "<redacted>"
    assert "80000" not in rendered
    assert "100000" not in rendered
    assert "000001.SZ" not in rendered
    leaves = list(_leaf_values(report))
    assert 80_000.0 not in leaves
    assert 100_000.0 not in leaves
    assert 1_000 not in leaves
    assert "000001.SZ" not in leaves
    assert not output.with_suffix(".json.tmp").exists()
    assert not (tmp_path / "runtime" / "qmt_probe.json.write_test").exists()


def test_handlebar_skips_historical_and_missing_last_bar_without_queries(tmp_path, monkeypatch):
    calls = []
    output = tmp_path / "qmt_probe.json"
    monkeypatch.setattr(qmt_probe, "PROBE_OUTPUT_PATH", str(output))
    monkeypatch.setattr(
        qmt_probe,
        "get_trade_detail_data",
        _successful_namespace(calls)["get_trade_detail_data"],
        raising=False,
    )
    monkeypatch.setattr(qmt_probe, "_PROBE_DONE", False)

    qmt_probe.handlebar(RealtimeContext(is_last=False))
    qmt_probe.handlebar(object())

    assert calls == []
    assert not output.exists()


def test_handlebar_runs_once_on_realtime_bar(tmp_path, monkeypatch):
    calls = []
    output = tmp_path / "qmt_probe.json"
    context = RealtimeContext(is_last=True)
    monkeypatch.setattr(qmt_probe, "PROBE_OUTPUT_PATH", str(output))
    monkeypatch.setattr(qmt_probe, "QMT_API_NAMESPACE", _successful_namespace(calls))
    monkeypatch.setattr(qmt_probe, "_PROBE_DONE", False)

    qmt_probe.handlebar(context)
    qmt_probe.handlebar(context)

    assert output.exists()
    assert [item[2] for item in calls] == ["account", "position", "order", "deal"]
    assert context.quote_calls == 1


def test_injected_namespace_is_preferred_and_remains_strictly_read_only(tmp_path, monkeypatch):
    calls = []
    submitted = []
    namespace = _successful_namespace(calls)

    def passorder(*args, **kwargs):
        submitted.append((args, kwargs))

    namespace["passorder"] = passorder
    monkeypatch.setattr(qmt_probe, "QMT_API_NAMESPACE", namespace)

    report = qmt_probe.run_probe(
        RealtimeContext(),
        output_path=str(tmp_path / "qmt_probe.json"),
    )

    assert [item[2] for item in calls] == ["account", "position", "order", "deal"]
    assert submitted == []
    assert "passorder" in report["capabilities"]["discovered_api_surface"]["globals"]


def test_order_deal_fields_and_cancel_signature_are_observed_without_calls(tmp_path):
    cancelled = []

    def cancel_order(order_id):
        cancelled.append(order_id)

    namespace = _successful_namespace(include_history=True)
    namespace["cancel_order"] = cancel_order

    report = qmt_probe.run_probe(
        RealtimeContext(),
        output_path=str(tmp_path / "qmt_probe.json"),
        namespace=namespace,
    )

    assert report["capabilities"]["order_query"]["status"] == "ok"
    assert report["capabilities"]["deal_query"]["status"] == "ok"
    assert report["observed_fields"]["order"] == {
        "order_id": "m_strOrderSysID",
        "remark": "m_strRemark",
        "status": "m_nOrderStatus",
        "order_volume": "m_nOrderVolume",
        "traded_volume": "m_nTradedVolume",
        "order_price": "m_dLimitPrice",
    }
    assert report["observed_fields"]["deal"] == {
        "deal_id": "m_strTradeID",
        "order_id": "m_strOrderSysID",
        "remark": "m_strRemark",
        "volume": "m_nVolume",
        "price": "m_dPrice",
        "fee": "m_dCommission",
    }
    assert report["capabilities"]["discovered_api_surface"]["globals"]["cancel_order"] == "(order_id)"
    assert cancelled == []
    rendered = (tmp_path / "qmt_probe.json").read_text(encoding="utf-8")
    assert "order-1" not in rendered
    assert "deal-1" not in rendered
    leaves = list(_leaf_values(report))
    assert 100 not in leaves
    assert 10.51 not in leaves
    assert "order-1" not in leaves
    assert "deal-1" not in leaves


def test_legitimate_empty_position_account_can_be_read_only_ready(tmp_path):
    def query(account_id, account_type, kind):
        if kind == "account":
            return [Account()]
        return []

    report = qmt_probe.run_probe(
        RealtimeContext(),
        output_path=str(tmp_path / "qmt_probe.json"),
        namespace={"get_trade_detail_data": query},
    )

    assert report["capabilities"]["position_query"]["status"] == "empty"
    assert report["observed_fields"]["position_empty"] is True
    assert report["read_only_requirements"]["position_shape"] is True
    assert report["ready"] is True


def test_empty_tick_object_cannot_be_marked_ready(tmp_path):
    class EmptyTickContext(RealtimeContext):
        def get_full_tick(self, symbols):
            return {symbol: {} for symbol in symbols}

    output = tmp_path / "qmt_probe.json"
    report = qmt_probe.run_probe(
        EmptyTickContext(),
        output_path=str(output),
        namespace=_successful_namespace(),
    )

    assert report["capabilities"]["quote_query"]["status"] == "ok"
    assert report["read_only_requirements"]["quote_shape"] is False
    assert report["ready"] is False
    assert "000001.SZ" not in output.read_text(encoding="utf-8")


def test_interface_error_is_recorded_and_never_marked_ready(tmp_path):
    def broken_query(account_id, account_type, kind):
        raise RuntimeError("broker unavailable")

    output = tmp_path / "qmt_probe.json"
    report = qmt_probe.run_probe(
        RealtimeContext(),
        output_path=str(output),
        namespace={"get_trade_detail_data": broken_query},
    )

    assert report["ready"] is False
    assert report["capabilities"]["account_query"]["status"] == "error"
    assert report["capabilities"]["position_query"]["status"] == "error"
    assert {item["item"] for item in report["errors"]} >= {"account_query", "position_query"}
    assert report["capabilities"]["account_query"]["error"] == "RuntimeError"


def test_dangerous_switch_still_cannot_submit_in_read_only_version(tmp_path, monkeypatch):
    submitted = []

    def passorder(*args, **kwargs):
        submitted.append((args, kwargs))
        raise AssertionError("passorder must never be called")

    monkeypatch.setattr(qmt_probe, "ENABLE_SIMULATED_ORDER_PROBE", True)
    monkeypatch.setattr(qmt_probe, "DANGEROUS_ORDER_PROBE_ACK", qmt_probe.REQUIRED_DANGEROUS_ACK)
    namespace = _successful_namespace()
    namespace["passorder"] = passorder

    report = qmt_probe.run_probe(
        RealtimeContext(),
        output_path=str(tmp_path / "qmt_probe.json"),
        namespace=namespace,
    )

    assert submitted == []
    assert report["mode"]["order_probe"]["status"] == "not_implemented"
    assert report["mode"]["order_probe"]["effective"] is False
    assert report["ready_for_trading"] is False

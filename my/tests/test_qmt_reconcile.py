from types import SimpleNamespace

from my.qmt import reconcile
from my.qmt.reconcile import compare_plans


def _order(shares=100):
    return {
        "order_id": "batch:buy:001",
        "code": "SH600001",
        "side": "buy",
        "shares": shares,
        "reference_price": 10.0,
        "submit_price": 10.03,
        "price_floor": 10.0,
        "price_ceiling": 10.03,
        "reason": "top100_entry",
        "candidate_rank": 1,
        "qmt_code": "600001.SH",
    }


def test_compare_plans_ignores_transport_only_fields():
    archived = _order()
    archived["broker_request_id"] = "123"

    comparison = compare_plans([_order()], [archived])

    assert comparison == {
        "match": True,
        "replayed_count": 1,
        "archived_count": 1,
        "differences": [],
    }


def test_compare_plans_reports_first_class_share_difference():
    comparison = compare_plans([_order(100)], [_order(200)])

    assert comparison["match"] is False
    assert comparison["differences"][0]["replayed"]["shares"] == 100
    assert comparison["differences"][0]["archived"]["shares"] == 200


def test_reconcile_uses_sell_and_buy_time_market_snapshots(monkeypatch, tmp_path):
    signal = {
        "batch_id": "batch-1",
        "exec_date": "2026-08-17",
        "account_alias": "qmt_sim",
    }
    result = {
        "batch_id": "batch-1",
        "market_snapshot": {"snapshot": "sell-time"},
        "buy_market_snapshot": {"snapshot": "buy-time"},
        "account_before": {"holdings": []},
        "account_after_sell": {"holdings": []},
        "sell_stage": {"planned": []},
        "buy_stage": {"planned": []},
    }
    observed = {}

    monkeypatch.setattr(reconcile, "read_json", lambda _path: signal)
    monkeypatch.setattr(reconcile, "validate_signal", lambda payload, **_kwargs: payload)
    monkeypatch.setattr(reconcile, "read_result", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(
        reconcile,
        "_market_for_planner",
        lambda payload, _date: payload["snapshot"],
    )
    monkeypatch.setattr(
        reconcile,
        "_account_for_planner",
        lambda _payload: SimpleNamespace(holdings={}),
    )
    monkeypatch.setattr(reconcile, "package_from_signal", lambda *_args: object())

    def fake_sells(_package, _account, market):
        observed["sell"] = market
        return SimpleNamespace(orders=[])

    def fake_buys(_package, _account, market):
        observed["buy"] = market
        return SimpleNamespace(orders=[])

    monkeypatch.setattr(reconcile, "plan_sells", fake_sells)
    monkeypatch.setattr(reconcile, "plan_buys", fake_buys)
    monkeypatch.setattr(reconcile, "_atomic_replace_json", lambda *_args: None)

    report = reconcile.reconcile_day(tmp_path, "2026-08-17")

    assert report["match"] is True
    assert observed == {"sell": "sell-time", "buy": "buy-time"}

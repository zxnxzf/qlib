from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from my.qmt import producer


TZ = timezone(timedelta(hours=8))
PROVENANCE = {
    "source_type": "published_model",
    "strategy_id": "lgb_alpha158_gate905_v1",
    "release_id": "2026Q3",
    "model_sha256": "1" * 64,
    "config_sha256": "2" * 64,
    "runtime_code_sha256": "3" * 64,
    "source_git_commit": "4" * 40,
}


def _payload(**overrides):
    kwargs = {
        "signal_date": "2026-08-14",
        "exec_date": "2026-08-17",
        "account_alias": "qmt_sim",
        "scores": pd.Series({"SH600002": 1.0, "SH600001": 2.0}, name="score"),
        "gate_on": True,
        "gate_note": "test gate",
        "provenance": PROVENANCE,
        "release_id": "2026Q3",
        "data_asof": "2026-08-14",
        "now": datetime(2026, 8, 14, 20, 0, tzinfo=TZ),
        "reference_closes": {"SH600001": 10.0, "SH600002": 20.0},
    }
    kwargs.update(overrides)
    return producer.build_payload(**kwargs)


def test_build_payload_contains_full_scores_and_ranked_top_candidates():
    payload = _payload()

    assert payload["schema_version"] == 2
    assert payload["batch_id"] == "2026-08-14_2026-08-17_2026Q3"
    assert payload["data_asof"] == payload["signal_date"]
    assert payload["gate"]["on"] is True
    assert payload["scores"] == {"SH600001": 2.0, "SH600002": 1.0}
    assert [item["code"] for item in payload["candidates"]] == ["SH600001", "SH600002"]
    assert payload["expires_at"] == "2026-08-17T09:32:00+08:00"
    assert "checksum" not in payload


def test_build_payload_rejects_stale_data_and_real_account_like_alias():
    with pytest.raises(ValueError, match="data_asof"):
        _payload(data_asof="2026-08-13")
    with pytest.raises(ValueError, match="account_alias"):
        _payload(account_alias="4100 1500")


def test_build_payload_rejects_creation_after_deadline():
    with pytest.raises(ValueError, match="deadline"):
        _payload(now=datetime(2026, 8, 17, 9, 32, tzinfo=TZ))


def test_build_payload_allows_the_full_09_31_execution_minute():
    payload = _payload(now=datetime(2026, 8, 17, 9, 31, 59, tzinfo=TZ))

    assert payload["expires_at"] == "2026-08-17T09:32:00+08:00"


def test_build_payload_requires_boolean_gate():
    with pytest.raises(TypeError, match="gate_on"):
        _payload(gate_on="False")


def test_generate_signal_reuses_valid_immutable_file(tmp_path, monkeypatch):
    class Published:
        release_id = "2026Q3"

    monkeypatch.setattr(producer.data, "latest_data_date", lambda: "2026-08-14")
    monkeypatch.setattr(producer.data, "next_trade_date", lambda _date: "2026-08-17")
    monkeypatch.setattr(producer.signal_, "validate_release", lambda _date: Published())
    monkeypatch.setattr(
        producer.signal_,
        "scores_for",
        lambda *_args, **_kwargs: pd.Series({"SH600001": 1.0}, name="score"),
    )
    monkeypatch.setattr(producer.signal_, "release_provenance", lambda _published: PROVENANCE)
    monkeypatch.setattr(producer.gate, "gate_for_next_day", lambda _date: (True, "on"))
    monkeypatch.setattr(producer, "_reference_closes", lambda _codes, _date: {"SH600001": 10.0})
    now = datetime(2026, 8, 14, 20, 0, tzinfo=TZ)

    first = producer.generate_signal(
        "2026-08-14", runtime_root=tmp_path, skip_update=True, now=now, log=lambda _msg: None
    )
    second = producer.generate_signal(
        "2026-08-14", runtime_root=tmp_path, skip_update=True, now=now, log=lambda _msg: None
    )

    assert first == second == Path(tmp_path) / "qmt_inbox" / "2026-08-17" / "signal.json"

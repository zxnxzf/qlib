import json
from dataclasses import replace

import pandas as pd
import pytest

from my.quant import signal_package
from my.quant.signal_package import (
    build_signal_package,
    load_signal_package,
    save_signal_package,
)


PARAMS = {
    "topk": 50,
    "candidate_limit": 100,
    "n_drop": 2,
    "risk_degree": 0.95,
    "lot": 100,
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


def _build(scores=None, holding_codes=None, batch_id="2026-08-05-v1"):
    scores = scores if scores is not None else pd.Series(
        {"SH600002": 2.0, "SH600001": 2.0, "SH600003": 1.0},
        name="score",
    )
    closes = {str(code): 10.0 + index for index, code in enumerate(scores.index)}
    return build_signal_package(
        scores=scores,
        signal_date="2026-08-04",
        exec_date="2026-08-05",
        gate_on=True,
        holding_codes=holding_codes or [],
        params=PARAMS,
        batch_id=batch_id,
        reference_closes=closes,
        provenance=PROVENANCE,
    )


def test_build_signal_package_keeps_only_top100_in_rank_order():
    scores = pd.Series(
        {f"SH{index:06d}": float(index) for index in range(120)},
        name="score",
    )

    package = _build(scores)

    assert len(package.candidates) == 100
    assert [candidate.code for candidate in package.candidates[:3]] == [
        "SH000119",
        "SH000118",
        "SH000117",
    ]
    assert package.candidates[-1].code == "SH000020"
    assert [candidate.rank for candidate in package.candidates] == list(range(1, 101))


def test_build_signal_package_breaks_score_ties_by_code():
    package = _build()

    assert [candidate.code for candidate in package.candidates] == [
        "SH600001",
        "SH600002",
        "SH600003",
    ]


def test_build_signal_package_locks_holding_scores_including_missing_values():
    package = _build(holding_codes=["SH600001", "SZ000999"])

    assert package.holding_scores == {"SH600001": 2.0, "SZ000999": None}


def test_signal_package_json_round_trip_and_atomic_rename(tmp_path):
    package = _build()

    path = save_signal_package(package, tmp_path)
    restored = load_signal_package("2026-08-05", tmp_path)

    assert path == tmp_path / "signals" / "2026-08-05.json"
    assert restored == package
    assert not path.with_suffix(".json.tmp").exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["checksum"]
    assert payload["schema_version"] == 2
    assert payload["content"]["provenance"] == PROVENANCE
    assert payload["content"]["candidates"][0]["reference_close"] == 11.0


def test_load_signal_package_rejects_requested_date_mismatch(tmp_path):
    signals_dir = tmp_path / "signals"
    signals_dir.mkdir()
    source = save_signal_package(_build(), tmp_path)
    wrong = signals_dir / "2026-08-06.json"
    wrong.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ValueError, match="执行日不一致"):
        load_signal_package("2026-08-06", tmp_path)


def test_load_signal_package_keeps_schema_v1_backward_compatibility(tmp_path):
    package = _build()
    content = signal_package._package_content(package)
    content.pop("provenance")
    signals_dir = tmp_path / "signals"
    signals_dir.mkdir()
    (signals_dir / "2026-08-05.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "checksum": signal_package._checksum(content),
                "content": content,
            }
        ),
        encoding="utf-8",
    )

    restored = load_signal_package("2026-08-05", tmp_path)

    assert restored.provenance == {}


def test_load_signal_package_rejects_checksum_tampering(tmp_path):
    path = save_signal_package(_build(), tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["content"]["gate_on"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="校验失败"):
        load_signal_package("2026-08-05", tmp_path)


def test_load_signal_package_rejects_missing_strategy_parameter(tmp_path):
    package = _build()
    invalid = replace(package, params={key: value for key, value in package.params.items() if key != "lot"})

    with pytest.raises(ValueError, match="缺少策略参数"):
        save_signal_package(invalid, tmp_path)


def test_build_signal_package_requires_reference_close_for_each_candidate():
    with pytest.raises(ValueError, match="参考收盘价"):
        build_signal_package(
            scores=pd.Series({"SH600001": 1.0}),
            signal_date="2026-08-04",
            exec_date="2026-08-05",
            gate_on=True,
            holding_codes=[],
            params=PARAMS,
            batch_id="2026-08-05-v1",
            reference_closes={},
        )


def test_save_signal_package_rejects_missing_release_provenance(tmp_path):
    package = replace(_build(), provenance={})

    with pytest.raises(ValueError, match="来源类型"):
        save_signal_package(package, tmp_path)

import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from my.quant import config as C
from my.quant import data, gate, ledger, nightly, signal_
from my.strategies.lgb_alpha158_gate905_v1 import workflow


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write_valid_release(package: Path, date: str = "2026-08-14"):
    rid = workflow.release_id(date)
    model = workflow.model_path(date, package)
    manifest_path = workflow.manifest_path(date, package)
    report = package / "releases" / f"{rid}-validation.md"
    model.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"test-native-lightgbm-model")
    report.write_text("approved test report\n", encoding="utf-8")
    columns = [["F0"], ["F1"]]
    manifest = {
        "schema_version": 1,
        "strategy_id": C.STRATEGY_ID,
        "release_id": rid,
        "status": "published",
        "config_sha256": workflow.config_sha256(),
        "runtime_code_sha256": workflow.runtime_code_sha256(),
        "source_git_commit": _git_head(),
        "model": {
            "path": f"models/{rid}.txt",
            "format": "lightgbm_native_booster",
            "sha256": _sha256(model),
        },
        "feature_schema": {
            "columns": columns,
            "count": len(columns),
            "sha256": workflow.feature_columns_sha256(columns),
        },
        "rolling_window": workflow.rolling_window(date).as_dict(),
        "validation": {
            "approved": True,
            "method": "archived_score_reconstruction",
            "report_path": f"releases/{rid}-validation.md",
            "report_sha256": _sha256(report),
            "approved_at": "2026-08-14T23:30:00+08:00",
            "archive_score_sha256": workflow.APPROVED_ARCHIVE_REFERENCES[rid]["sha256"],
            "overlap": workflow.APPROVED_ARCHIVE_REFERENCES[rid]["overlap"],
            "max_abs_diff": 0.0,
            "top_n": workflow.APPROVED_ARCHIVE_REFERENCES[rid]["top_n"],
            "min_daily_top_overlap": 1.0,
            "extra_in_rebuilt": 0,
            "min_daily_production_top_overlap": 1.0,
        },
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return model, manifest_path, report


def test_runtime_uses_locked_strategy_package():
    assert C.STRATEGY_DIR.name == C.STRATEGY_ID == "lgb_alpha158_gate905_v1"
    assert C.POOL == "all_no_bj"
    assert C.LABEL_EXPR == "Ref($close, -6) / Ref($close, -1) - 1"
    assert (C.TOPK, C.CANDIDATE_LIMIT, C.N_DROP, C.HOLD_THRESH) == (50, 100, 2, 1)
    assert C.ONLY_TRADABLE is True
    assert C.RISK_DEGREE == 0.95
    assert (C.DEAL_PRICE, C.LOT) == ("open", 100)
    assert (C.GATE_INDEX, C.GATE_MA, C.GATE_CONFIRM_DAYS) == ("SH000905", 20, 3)
    assert C.GATE_SURGE_REENTRY == 0.025
    assert C.SHADOW_INIT_CASH == 100_000.0
    assert (C.OPEN_COST, C.CLOSE_COST, C.MIN_COST, C.IMPACT_COST) == (
        0.0005,
        0.0015,
        5.0,
        0.001,
    )
    assert (C.MAX_SLIPPAGE, C.EXECUTION_WAIT_SECONDS) == (0.003, 30)


def test_release_hash_covers_alpha158_feature_implementation():
    paths = {path.relative_to(C.REPO).as_posix() for path in workflow._runtime_code_paths()}

    assert {
        "qlib/contrib/data/handler.py",
        "qlib/contrib/data/loader.py",
        "qlib/data/ops.py",
        "qlib/data/dataset/handler.py",
        "qlib/data/dataset/loader.py",
        "qlib/data/dataset/processor.py",
        "qlib/data/dataset/utils.py",
    }.issubset(paths)


def test_rolling_window_strictly_excludes_q_minus_7_boundary():
    window = workflow.rolling_window("2026-08-14")
    assert window.as_dict() == {
        "train_start": "2023-01-01",
        "train_end_exclusive": "2026-01-01",
        "validation_start": "2026-01-01",
        "validation_end_exclusive": "2026-06-24",
        "prediction_start": "2026-07-01",
        "prediction_end_exclusive": "2026-10-01",
    }
    dates = pd.to_datetime(
        ["2022-12-31", "2023-01-01", "2025-12-31", "2026-01-01", "2026-06-23", "2026-06-24"]
    )
    index = pd.MultiIndex.from_product([dates, ["SH600000"]], names=["datetime", "instrument"])
    columns = pd.MultiIndex.from_tuples([("feature", "F0"), ("label", "LABEL0")])
    learn = pd.DataFrame([[1.0, 1.0]] * len(index), index=index, columns=columns)

    train, validation = workflow.split_learning_data(learn, ("label", "LABEL0"), window)

    assert list(train.index.get_level_values("datetime")) == list(
        pd.to_datetime(["2023-01-01", "2025-12-31"])
    )
    assert list(validation.index.get_level_values("datetime")) == list(
        pd.to_datetime(["2026-01-01", "2026-06-23"])
    )


def test_training_and_scoring_feature_schema_share_one_canonical_shape():
    training_columns = pd.MultiIndex.from_tuples([("feature", "F0"), ("feature", "F1")])
    scoring_columns = pd.Index(["F0", "F1"])

    assert workflow.normalize_feature_columns(training_columns) == [["F0"], ["F1"]]
    assert workflow.normalize_feature_columns(scoring_columns) == [["F0"], ["F1"]]
    assert workflow.feature_columns_sha256(training_columns) == workflow.feature_columns_sha256(
        [["F0"], ["F1"]]
    )


def test_candidate_must_reproduce_archived_scores_before_promotion(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    candidate_model = candidate / "model.txt"
    candidate_model.write_text("native model", encoding="utf-8")
    index = pd.MultiIndex.from_product(
        [pd.to_datetime(["2026-07-01", "2026-07-02"]), ["SH600000", "SZ000001", "SZ000002"]],
        names=["datetime", "instrument"],
    )
    features = pd.DataFrame({"F0": [0.6, 0.4, 0.2, 0.1, 0.3, 0.5]}, index=index)
    archive = features.rename(columns={"F0": "score"})
    archive_path = tmp_path / "archive.pkl"
    archive.to_pickle(archive_path)
    monkeypatch.setitem(
        workflow.APPROVED_ARCHIVE_REFERENCES,
        "2026Q3",
        {
            "path": str(archive_path),
            "sha256": _sha256(archive_path),
            "overlap": {"start": "2026-07-01", "end": "2026-07-02", "rows": 6, "days": 2},
            "top_n": 2,
            "absolute_tolerance": 1e-12,
        },
    )
    metadata = {
        "release_id": "2026Q3",
        "model": {"sha256": _sha256(candidate_model)},
        "feature_schema": {"columns": [["F0"]]},
    }

    class Booster:
        def predict(self, values):
            return values[:, 0]

    class Handler:
        def fetch(self, **_kwargs):
            return features

    monkeypatch.setattr(
        workflow,
        "_load_candidate",
        lambda *_args, **_kwargs: (candidate, metadata, candidate_model, Booster()),
    )
    monkeypatch.setattr(workflow, "_build_handler", lambda *_args: Handler())

    evidence_path = workflow.validate_candidate_against_archive(
        "2026-08-14", archive_path, output_dir=candidate, log=lambda _message: None
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    assert evidence["status"] == "passed"
    assert evidence["overlap"] == {
        "start": "2026-07-01",
        "end": "2026-07-02",
        "rows": 6,
        "days": 2,
    }
    assert evidence["comparison"]["max_abs_diff"] == 0.0
    assert evidence["comparison"]["min_daily_top_overlap"] == 1.0


def test_promote_candidate_requires_passed_evidence_and_writes_manifest(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate"
    package = tmp_path / "package"
    candidate.mkdir()
    (package / "releases").mkdir(parents=True)
    candidate_model = candidate / "model.txt"
    candidate_model.write_text("native model", encoding="utf-8")
    model_hash = _sha256(candidate_model)
    metadata = {
        "strategy_id": C.STRATEGY_ID,
        "release_id": "2026Q3",
        "config_sha256": "1" * 64,
        "runtime_code_sha256": "2" * 64,
        "source_git_commit": _git_head(),
        "model": {"sha256": model_hash, "best_iteration": 17},
        "feature_schema": {"columns": [["F0"]], "count": 1, "sha256": "3" * 64},
        "rolling_window": workflow.rolling_window("2026-08-14").as_dict(),
        "row_counts": {"train": 100, "validation": 20},
    }
    evidence = {
        "schema_version": 1,
        "status": "passed",
        "strategy_id": C.STRATEGY_ID,
        "release_id": "2026Q3",
        "candidate_model_sha256": model_hash,
        "archive_path": str(tmp_path / "archive.pkl"),
        "archive_sha256": "4" * 64,
        "overlap": {"start": "2026-07-01", "end": "2026-07-28", "rows": 20, "days": 20},
        "comparison": {
            "missing_in_rebuilt": 0,
            "extra_in_rebuilt": 0,
            "max_abs_diff": 0.0,
            "top_n": 100,
            "min_daily_top_overlap": 1.0,
            "daily_top_overlap": {f"day-{index}": 1.0 for index in range(20)},
            "min_daily_production_top_overlap": 1.0,
            "daily_production_top_overlap": {f"day-{index}": 1.0 for index in range(20)},
        },
    }
    (candidate / "validation.json").write_text(json.dumps(evidence), encoding="utf-8")
    report = package / "releases" / "2026Q3-validation.md"
    report.write_text("approved\n", encoding="utf-8")
    monkeypatch.setattr(
        workflow,
        "_load_candidate",
        lambda *_args, **_kwargs: (candidate, metadata, candidate_model, object()),
    )
    monkeypatch.setitem(
        workflow.APPROVED_ARCHIVE_REFERENCES,
        "2026Q3",
        {
            "path": str(tmp_path / "archive.pkl"),
            "sha256": "4" * 64,
            "overlap": {"start": "2026-07-01", "end": "2026-07-28", "rows": 20, "days": 20},
            "top_n": 100,
            "absolute_tolerance": 1e-12,
        },
    )
    monkeypatch.setattr(
        workflow,
        "validate_candidate_against_archive",
        lambda *_args, **_kwargs: candidate / "validation.json",
    )

    manifest_path = workflow.promote_candidate(
        "2026-08-14",
        output_dir=candidate,
        package_dir=package,
        approved_at="2026-08-14T20:00:00+08:00",
        log=lambda _message: None,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert (package / "models" / "2026Q3.txt").read_bytes() == candidate_model.read_bytes()
    assert manifest["status"] == "published"
    assert manifest["model"]["sha256"] == model_hash
    assert manifest["validation"]["archive_score_sha256"] == "4" * 64
    assert manifest["validation"]["report_sha256"] == _sha256(report)


@pytest.mark.parametrize(
    ("date", "rid"),
    [
        ("2026-01-01", "2026Q1"),
        ("2026-03-31", "2026Q1"),
        ("2026-04-01", "2026Q2"),
        ("2026-07-01", "2026Q3"),
        ("2026-09-30", "2026Q3"),
        ("2026-10-01", "2026Q4"),
        ("2026-12-31", "2026Q4"),
    ],
)
def test_quarter_routes_to_versioned_model_and_manifest(tmp_path, date, rid):
    assert workflow.release_id(date) == rid
    assert workflow.model_path(date, tmp_path) == tmp_path / "models" / f"{rid}.txt"
    assert workflow.manifest_path(date, tmp_path) == tmp_path / "releases" / f"{rid}.json"


def test_valid_published_release_is_accepted(tmp_path, monkeypatch):
    import lightgbm as lgb

    model, manifest, _report = _write_valid_release(tmp_path)
    monkeypatch.setattr(lgb, "Booster", lambda model_file: type("Booster", (), {"num_feature": lambda self: 2})())

    published = workflow.validate_release("2026-08-14", package_dir=tmp_path)

    assert published.release_id == "2026Q3"
    assert published.model_path == model.resolve()
    assert published.manifest_path == manifest
    assert workflow.release_provenance(published) == {
        "source_type": "published_model",
        "strategy_id": C.STRATEGY_ID,
        "release_id": "2026Q3",
        "model_sha256": published.manifest["model"]["sha256"],
        "config_sha256": published.manifest["config_sha256"],
        "runtime_code_sha256": published.manifest["runtime_code_sha256"],
        "source_git_commit": published.manifest["source_git_commit"],
    }


def test_corrupt_native_model_is_rejected_even_when_hash_matches(tmp_path):
    _write_valid_release(tmp_path)

    with pytest.raises(workflow.ReleaseValidationError, match="不是可加载"):
        workflow.validate_release("2026-08-14", package_dir=tmp_path)


def test_scoring_rejects_alpha158_feature_order_drift(tmp_path, monkeypatch):
    model = tmp_path / "model.txt"
    model.write_text("test", encoding="utf-8")
    expected_columns = [["F0"], ["F1"]]
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-08-14"), "SH600000")],
        names=["datetime", "instrument"],
    )
    drifted = pd.DataFrame(
        [[1.0, 2.0]],
        index=index,
        columns=pd.Index(["F1", "F0"]),
    )

    class Handler:
        def fetch(self, **_kwargs):
            return drifted

    class Booster:
        def num_feature(self):
            return 2

        def predict(self, _values):
            raise AssertionError("feature drift must fail before prediction")

    published = workflow.PublishedRelease(
        release_id="2026Q3",
        model_path=model,
        manifest_path=tmp_path / "manifest.json",
        manifest={"feature_schema": {"columns": expected_columns}},
        booster=Booster(),
    )

    monkeypatch.setattr(workflow, "validate_release", lambda _date: published)
    monkeypatch.setattr(workflow, "_build_handler", lambda _start, _end: Handler())

    with pytest.raises(workflow.ReleaseValidationError, match="特征列或顺序"):
        workflow.scores_for("2026-08-14")


def test_scoring_accepts_real_single_level_alpha158_columns(tmp_path, monkeypatch):
    model = tmp_path / "model.txt"
    model.write_text("test", encoding="utf-8")
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-08-14"), "SH600000")],
        names=["datetime", "instrument"],
    )
    features = pd.DataFrame([[1.0, 2.0]], index=index, columns=pd.Index(["F0", "F1"]))

    class Handler:
        def fetch(self, **_kwargs):
            return features

    class Booster:
        def num_feature(self):
            return 2

        def predict(self, values):
            assert values.tolist() == [[1.0, 2.0]]
            return [0.25]

    published = workflow.PublishedRelease(
        release_id="2026Q3",
        model_path=model,
        manifest_path=tmp_path / "manifest.json",
        manifest={"feature_schema": {"columns": [["F0"], ["F1"]]}},
        booster=Booster(),
    )
    monkeypatch.setattr(workflow, "_build_handler", lambda _start, _end: Handler())

    scores = workflow.scores_for("2026-08-14", published=published, log=lambda _message: None)

    assert scores.to_dict() == {"SH600000": 0.25}


@pytest.mark.parametrize(
    "fault",
    [
        "missing_manifest",
        "missing_model",
        "model_hash",
        "config_hash",
        "runtime_hash",
        "source_commit",
        "strategy_id",
        "release_id",
        "feature_hash",
        "report_path",
        "missing_report",
        "unapproved",
    ],
)
def test_invalid_release_is_rejected(tmp_path, fault):
    model, manifest_path, report = _write_valid_release(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if fault == "missing_manifest":
        manifest_path.unlink()
    elif fault == "missing_model":
        model.unlink()
    elif fault == "model_hash":
        model.write_bytes(model.read_bytes() + b"tampered")
    elif fault == "config_hash":
        manifest["config_sha256"] = "0" * 64
    elif fault == "runtime_hash":
        manifest["runtime_code_sha256"] = "0" * 64
    elif fault == "source_commit":
        manifest["source_git_commit"] = "a" * 40
    elif fault == "strategy_id":
        manifest["strategy_id"] = "another_strategy"
    elif fault == "release_id":
        manifest["release_id"] = "2026Q2"
    elif fault == "feature_hash":
        manifest["feature_schema"]["sha256"] = "0" * 64
    elif fault == "report_path":
        manifest["validation"]["report_path"] = "releases/custom-report.html"
    elif fault == "missing_report":
        report.unlink()
    elif fault == "unapproved":
        manifest["validation"]["approved"] = False
    if manifest_path.exists():
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(workflow.ReleaseValidationError):
        workflow.validate_release("2026-08-14", package_dir=tmp_path)


def test_prepare_fails_before_writing_signal_or_state_when_release_is_invalid(tmp_path, monkeypatch):
    state_dir = tmp_path / "shadow"
    monkeypatch.setattr(C, "STATE_DIR", state_dir)
    ledger.save_state(
        {
            "cash": 100_000.0,
            "holdings": {},
            "last_prices": {},
            "last_settled": None,
            "pending_exec_date": None,
            "pending_signal_date": None,
            "pending_batch_id": None,
            "phase": "idle",
        }
    )
    state_path = state_dir / "state.json"
    original_state = state_path.read_bytes()
    monkeypatch.setattr(data, "latest_data_date", lambda: "2026-08-14")
    monkeypatch.setattr(data, "next_trade_date", lambda _date: "2026-08-17")
    monkeypatch.setattr(
        signal_,
        "validate_release",
        lambda _date: (_ for _ in ()).throw(workflow.ReleaseValidationError("missing release")),
    )
    monkeypatch.setattr(
        signal_,
        "train_candidate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not train")),
    )
    monkeypatch.setattr(
        gate,
        "gate_for_next_day",
        lambda _date: (_ for _ in ()).throw(AssertionError("gate must run after release validation")),
    )

    with pytest.raises(workflow.ReleaseValidationError, match="missing release"):
        nightly.prepare(asof="2026-08-14", skip_update=True)

    assert state_path.read_bytes() == original_state
    assert not list((state_dir / "signals").glob("*.json"))


def test_production_paths_are_repository_relative_and_cross_platform():
    assert C.REPO == Path(C.__file__).resolve().parents[2]
    assert C.DATA_DIR == C.REPO / "my" / "data" / "cn_data"
    assert C.STATE_DIR == C.REPO / "my" / "quant_state"
    assert C.STRATEGY_DIR == C.REPO / "my" / "strategies" / C.STRATEGY_ID
    assert C.MODEL_DIR == C.STRATEGY_DIR / "models"
    assert Path(C.UPDATE_SCRIPT) == C.REPO / "my" / "scripts" / "update_research_data.py"
    assert Path(C.VENV_PY).relative_to(C.REPO / ".venv")
    for path in [
        C.REPO / "my" / "quant" / "config.py",
        C.REPO / "my" / "scripts" / "shadow_run.py",
        C.REPO / "my" / "scripts" / "update_research_data.py",
        C.STRATEGY_DIR / "workflow.py",
    ]:
        assert "/Users/bytedance" not in path.read_text(encoding="utf-8")

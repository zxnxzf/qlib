"""Quarterly training and published-model scoring for this strategy package.

Daily shadow/QMT code only calls :func:`validate_release` and
:func:`scores_for`.  Candidate training is an explicit research operation and
never runs as a fallback from production.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from my.quant import config as C
from my.quant import data


class ReleaseValidationError(RuntimeError):
    """A quarterly model is absent, unpublished, or inconsistent."""


@dataclass(frozen=True)
class RollingWindow:
    release_id: str
    train_start: str
    train_end_exclusive: str
    validation_start: str
    validation_end_exclusive: str
    prediction_start: str
    prediction_end_exclusive: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "train_start": self.train_start,
            "train_end_exclusive": self.train_end_exclusive,
            "validation_start": self.validation_start,
            "validation_end_exclusive": self.validation_end_exclusive,
            "prediction_start": self.prediction_start,
            "prediction_end_exclusive": self.prediction_end_exclusive,
        }


@dataclass(frozen=True)
class PublishedRelease:
    release_id: str
    model_path: Path
    manifest_path: Path
    manifest: dict
    booster: Optional[object] = None


def quarter_start(date: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(date)
    return pd.Timestamp(timestamp.year, (timestamp.quarter - 1) * 3 + 1, 1)


def release_id(date_or_quarter) -> str:
    quarter = quarter_start(str(date_or_quarter))
    return f"{quarter.year}Q{quarter.quarter}"


def rolling_window(for_date: str) -> RollingWindow:
    quarter = quarter_start(for_date)
    train_start = quarter - pd.DateOffset(months=C.TRAIN_MONTHS)
    train_end = quarter - pd.DateOffset(months=C.VALID_MONTHS)
    validation_end = quarter - pd.Timedelta(days=C.VALID_GAP_DAYS)
    prediction_end = quarter + pd.DateOffset(months=3)
    return RollingWindow(
        release_id=release_id(quarter),
        train_start=train_start.strftime("%Y-%m-%d"),
        train_end_exclusive=train_end.strftime("%Y-%m-%d"),
        validation_start=train_end.strftime("%Y-%m-%d"),
        validation_end_exclusive=validation_end.strftime("%Y-%m-%d"),
        prediction_start=quarter.strftime("%Y-%m-%d"),
        prediction_end_exclusive=prediction_end.strftime("%Y-%m-%d"),
    )


def model_path(quarter, package_dir: Optional[Path] = None) -> Path:
    package = Path(package_dir) if package_dir is not None else C.STRATEGY_DIR
    return package / "models" / f"{release_id(quarter)}.txt"


def manifest_path(quarter, package_dir: Optional[Path] = None) -> Path:
    package = Path(package_dir) if package_dir is not None else C.STRATEGY_DIR
    return package / "releases" / f"{release_id(quarter)}.json"


def _canonical_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _json_sha256(value) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def config_sha256() -> str:
    """Stable across macOS/Windows line endings and checkout locations."""
    return _json_sha256(
        {
            "workflow.yaml": C.WORKFLOW_CONFIG,
            "rolling.yaml": C.ROLLING_CONFIG,
            "strategy.yaml": C.STRATEGY_CONFIG,
        }
    )


def _runtime_code_paths() -> List[Path]:
    return [
        C.STRATEGY_DIR / "workflow.py",
        C.REPO / "qlib" / "contrib" / "data" / "handler.py",
        C.REPO / "qlib" / "contrib" / "data" / "loader.py",
        C.REPO / "qlib" / "data" / "ops.py",
        C.REPO / "qlib" / "data" / "dataset" / "handler.py",
        C.REPO / "qlib" / "data" / "dataset" / "loader.py",
        C.REPO / "qlib" / "data" / "dataset" / "processor.py",
        C.REPO / "qlib" / "data" / "dataset" / "utils.py",
        C.REPO / "my" / "quant" / "config.py",
        C.REPO / "my" / "quant" / "signal_.py",
        C.REPO / "my" / "quant" / "gate.py",
        C.REPO / "my" / "quant" / "signal_package.py",
        C.REPO / "my" / "quant" / "trade_planner.py",
        C.REPO / "my" / "quant" / "nightly.py",
        C.REPO / "my" / "quant" / "execution.py",
        C.REPO / "my" / "quant" / "qlib_adapter.py",
    ]


def runtime_code_sha256() -> str:
    """Bind a release to the scoring, gate, planning, and execution code."""
    hashes = {}
    for path in _runtime_code_paths():
        relative = path.relative_to(C.REPO).as_posix()
        normalized = path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")
        hashes[relative] = hashlib.sha256(normalized).hexdigest()
    return _json_sha256(hashes)


def normalize_feature_columns(columns: Iterable) -> List[List[str]]:
    normalized = []
    for column in columns:
        parts = list(column) if isinstance(column, (tuple, list)) else [column]
        parts = [str(part) for part in parts]
        if len(parts) > 1 and parts[0] == "feature":
            parts = parts[1:]
        normalized.append(parts)
    return normalized


def feature_columns_sha256(columns: Iterable) -> str:
    return _json_sha256(normalize_feature_columns(columns))


def split_learning_data(
    learn: pd.DataFrame, label_column, window: RollingWindow
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Apply the exact research boundaries; q-7d is excluded."""
    dates = learn.index.get_level_values("datetime")
    train = learn[
        (dates >= pd.Timestamp(window.train_start))
        & (dates < pd.Timestamp(window.train_end_exclusive))
    ]
    validation = learn[
        (dates >= pd.Timestamp(window.validation_start))
        & (dates < pd.Timestamp(window.validation_end_exclusive))
    ]
    return train[train[label_column].notna()], validation[validation[label_column].notna()]


def _inside_package(package: Path, relative_path: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise ReleaseValidationError("发布清单中的路径必须是策略包内的相对路径")
    resolved_package = package.resolve()
    resolved = (package / relative_path).resolve()
    try:
        resolved.relative_to(resolved_package)
    except ValueError as exc:
        raise ReleaseValidationError(f"发布清单路径越出策略包: {relative_path}") from exc
    return resolved


def _required(mapping: dict, *keys):
    value = mapping
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise ReleaseValidationError(f"发布清单缺少字段: {'.'.join(keys)}")
        value = value[key]
    return value


def _validate_source_commit(commit: str) -> None:
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", commit) is None:
        raise ReleaseValidationError("发布清单缺少训练/回测所用的有效 Git 提交")
    exists = subprocess.run(
        ["git", "-C", str(C.REPO), "cat-file", "-e", f"{commit}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    if exists.returncode != 0:
        raise ReleaseValidationError(f"发布清单引用的 Git 提交在本仓库不存在: {commit}")
    ancestor = subprocess.run(
        ["git", "-C", str(C.REPO), "merge-base", "--is-ancestor", commit, "HEAD"],
        capture_output=True,
        text=True,
    )
    if ancestor.returncode != 0:
        raise ReleaseValidationError(f"当前生产版本不包含模型的来源提交: {commit}")


def _clean_source_commit() -> str:
    status = subprocess.run(
        [
            "git",
            "-C",
            str(C.REPO),
            "status",
            "--porcelain",
            "--untracked-files=normal",
            "--",
            "my/strategies",
            "my/quant",
            "qlib/contrib/data",
            "qlib/data/ops.py",
            "qlib/data/dataset",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if status.stdout.strip():
        raise RuntimeError("候选训练前必须先提交策略包和运行代码，禁止从未提交源码训练正式候选")
    return subprocess.run(
        ["git", "-C", str(C.REPO), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _validate_release_files_in_git(paths: Sequence[Path]) -> None:
    """Production releases must be tracked by HEAD and locally unmodified."""
    relative_paths = []
    repository = C.REPO.resolve()
    for path in paths:
        try:
            relative_paths.append(path.resolve().relative_to(repository).as_posix())
        except ValueError as exc:
            raise ReleaseValidationError(f"正式发布文件不在 Git 仓库内: {path}") from exc

    tracked = subprocess.run(
        ["git", "-C", str(C.REPO), "ls-files", "--error-unmatch", "--", *relative_paths],
        capture_output=True,
        text=True,
    )
    if tracked.returncode != 0:
        raise ReleaseValidationError("模型、发布清单或验证报告尚未提交 Git")
    status = subprocess.run(
        [
            "git",
            "-C",
            str(C.REPO),
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            *relative_paths,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if status.stdout.strip():
        raise ReleaseValidationError("模型、发布清单或验证报告存在未提交修改")


def validate_release(for_date: str, package_dir: Optional[Path] = None) -> PublishedRelease:
    """Validate the model, manifest, configs, feature schema, and approval."""
    package = Path(package_dir) if package_dir is not None else C.STRATEGY_DIR
    expected_release = release_id(for_date)
    expected_model = model_path(for_date, package)
    expected_manifest = manifest_path(for_date, package)

    if not expected_manifest.is_file():
        raise ReleaseValidationError(
            f"{expected_release} 未发布：缺少发布清单 {expected_manifest}；日常影子不会自动训练"
        )
    try:
        manifest = json.loads(expected_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseValidationError(f"发布清单不可读: {expected_manifest}") from exc

    if manifest.get("schema_version") != 1:
        raise ReleaseValidationError(f"不支持的发布清单版本: {manifest.get('schema_version')}")
    if manifest.get("strategy_id") != C.STRATEGY_ID:
        raise ReleaseValidationError("发布清单 strategy_id 与当前策略不一致")
    if manifest.get("release_id") != expected_release:
        raise ReleaseValidationError("发布清单季度与信号日不一致")
    if manifest.get("status") != "published":
        raise ReleaseValidationError(f"{expected_release} 尚未批准发布")
    if manifest.get("config_sha256") != config_sha256():
        raise ReleaseValidationError("策略配置已变化，发布清单 config_sha256 失效")
    if manifest.get("runtime_code_sha256") != runtime_code_sha256():
        raise ReleaseValidationError("评分/门控/规划器代码已变化，发布清单 runtime_code_sha256 失效")

    model_relative = _required(manifest, "model", "path")
    if model_relative != f"models/{expected_release}.txt":
        raise ReleaseValidationError("发布清单模型路径与季度约定不一致")
    manifest_model = _inside_package(package, model_relative)
    if manifest_model != expected_model.resolve() or not manifest_model.is_file():
        raise ReleaseValidationError(f"{expected_release} 模型文件不存在: {expected_model}")
    if _required(manifest, "model", "format") != "lightgbm_native_booster":
        raise ReleaseValidationError("正式模型必须是 LightGBM native Booster 文本格式")
    if _file_sha256(manifest_model) != _required(manifest, "model", "sha256"):
        raise ReleaseValidationError(f"{expected_release} 模型 SHA-256 校验失败")

    feature_schema = _required(manifest, "feature_schema")
    columns = _required(feature_schema, "columns")
    if not isinstance(columns, list) or not columns:
        raise ReleaseValidationError("发布清单缺少有序特征列")
    if normalize_feature_columns(columns) != columns:
        raise ReleaseValidationError("发布清单特征列不是标准评分格式")
    if _required(feature_schema, "count") != len(columns):
        raise ReleaseValidationError("发布清单特征列数不一致")
    if feature_columns_sha256(columns) != _required(feature_schema, "sha256"):
        raise ReleaseValidationError("发布清单特征顺序哈希不一致")

    expected_window = rolling_window(for_date).as_dict()
    if _required(manifest, "rolling_window") != expected_window:
        raise ReleaseValidationError("发布清单训练/验证窗口与 rolling.yaml 不一致")

    validation = _required(manifest, "validation")
    if validation.get("approved") is not True:
        raise ReleaseValidationError(f"{expected_release} 没有通过回测批准")
    report_relative = validation.get("report_path")
    if report_relative != f"releases/{expected_release}-validation.md":
        raise ReleaseValidationError("发布验证报告必须使用约定的Markdown路径")
    report_path = _inside_package(package, report_relative)
    if not report_path.is_file():
        raise ReleaseValidationError(f"发布验证报告不存在: {report_relative}")
    if _file_sha256(report_path) != validation.get("report_sha256"):
        raise ReleaseValidationError("发布验证报告 SHA-256 校验失败")
    approved_at = validation.get("approved_at")
    if not isinstance(approved_at, str) or not approved_at.strip():
        raise ReleaseValidationError("发布清单缺少批准时间")
    git_commit = manifest.get("source_git_commit")
    if not isinstance(git_commit, str):
        raise ReleaseValidationError("发布清单缺少训练/回测所用的有效 Git 提交")
    _validate_source_commit(git_commit.strip())
    if package.resolve() == C.STRATEGY_DIR.resolve():
        _validate_release_files_in_git(
            [
                expected_manifest,
                manifest_model,
                report_path,
                C.STRATEGY_DIR / "workflow.yaml",
                C.STRATEGY_DIR / "rolling.yaml",
                C.STRATEGY_DIR / "strategy.yaml",
                *_runtime_code_paths(),
            ]
        )

    try:
        import lightgbm as lgb

        booster = lgb.Booster(model_file=str(manifest_model))
    except Exception as exc:
        raise ReleaseValidationError(f"{expected_release} 不是可加载的 LightGBM native Booster") from exc
    if booster.num_feature() != len(columns):
        raise ReleaseValidationError(
            f"{expected_release} 模型特征数 {booster.num_feature()} != 发布清单 {len(columns)}"
        )

    return PublishedRelease(expected_release, manifest_model, expected_manifest, manifest, booster)


def release_provenance(published: PublishedRelease) -> Dict[str, str]:
    """Compact immutable identity embedded in each production signal package."""
    return {
        "source_type": "published_model",
        "strategy_id": str(published.manifest["strategy_id"]),
        "release_id": str(published.release_id),
        "model_sha256": str(_required(published.manifest, "model", "sha256")),
        "config_sha256": str(published.manifest["config_sha256"]),
        "runtime_code_sha256": str(published.manifest["runtime_code_sha256"]),
        "source_git_commit": str(published.manifest["source_git_commit"]),
    }


def _build_handler(start: str, end: str):
    from qlib.contrib.data.handler import Alpha158

    data.init_qlib()
    return Alpha158(
        instruments=C.POOL,
        start_time=start,
        end_time=end,
        fit_start_time=start,
        fit_end_time=end,
        label=[C.LABEL_EXPR],
    )


def scores_for(date: str, log=print, published: Optional[PublishedRelease] = None) -> pd.Series:
    """Score one signal day with a verified quarterly release."""
    published = published or validate_release(date)

    from qlib.data.dataset.handler import DataHandlerLP

    booster = published.booster
    if booster is None:
        raise ReleaseValidationError(f"{published.release_id} 已发布模型未通过加载校验")
    start = (pd.Timestamp(date) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    handler = _build_handler(start, date)
    infer = handler.fetch(col_set="feature", data_key=DataHandlerLP.DK_I)
    dates = infer.index.get_level_values("datetime")
    day = infer[dates == pd.Timestamp(date)]
    if day.empty:
        raise RuntimeError(f"{date} 无特征数据")

    actual_columns = normalize_feature_columns(day.columns)
    expected_columns = published.manifest["feature_schema"]["columns"]
    if actual_columns != expected_columns:
        raise ReleaseValidationError(
            f"{published.release_id} Alpha158 特征列或顺序与发布清单不一致"
        )
    if booster.num_feature() != len(actual_columns):
        raise ReleaseValidationError(
            f"{published.release_id} 模型特征数 {booster.num_feature()} != {len(actual_columns)}"
        )

    scores = pd.Series(
        booster.predict(day.values),
        index=day.index.get_level_values("instrument"),
        name="score",
    )
    log(f"[signal] {date} 打分 {len(scores)} 只（正式发布 {published.release_id}）")
    return scores


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def train_candidate(
    for_date: str,
    output_dir: Optional[Path] = None,
    overwrite: bool = False,
    log=print,
) -> Path:
    """Explicitly train an unapproved native Booster under my/artifacts/."""
    window = rolling_window(for_date)
    target = (
        Path(output_dir)
        if output_dir is not None
        else C.ARTIFACTS / "strategy_candidates" / C.STRATEGY_ID / window.release_id
    )
    candidate_model = target / "model.txt"
    candidate_metadata = target / "candidate.json"
    if not overwrite and (candidate_model.exists() or candidate_metadata.exists()):
        raise FileExistsError(f"候选目录已有结果，拒绝覆盖: {target}")
    source_git_commit = _clean_source_commit()

    import lightgbm as lgb
    from qlib.data.dataset.handler import DataHandlerLP

    log(
        f"[candidate] {window.release_id}: train [{window.train_start}, {window.train_end_exclusive}), "
        f"valid [{window.validation_start}, {window.validation_end_exclusive})"
    )
    handler = _build_handler(window.train_start, window.validation_end_exclusive)
    learn = handler.fetch(col_set=["feature", "label"], data_key=DataHandlerLP.DK_L)
    feature_columns = [column for column in learn.columns if column[0] == "feature"]
    scoring_feature_columns = handler.get_cols(col_set="feature", data_key=DataHandlerLP.DK_I)
    label_column = [column for column in learn.columns if column[0] == "label"][0]
    train, validation = split_learning_data(learn, label_column, window)
    if train.empty or validation.empty:
        raise RuntimeError(f"{window.release_id} 训练集或验证集为空")

    training_set = lgb.Dataset(train[feature_columns].values, label=train[label_column].values)
    validation_set = lgb.Dataset(
        validation[feature_columns].values,
        label=validation[label_column].values,
        reference=training_set,
    )
    booster = lgb.train(
        C.LGB_PARAMS,
        training_set,
        num_boost_round=C.NUM_BOOST_ROUND,
        valid_sets=[validation_set],
        callbacks=[lgb.early_stopping(C.EARLY_STOP, verbose=False)],
    )

    target.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(candidate_model), num_iteration=booster.best_iteration)
    normalized_columns = normalize_feature_columns(scoring_feature_columns)
    training_columns = normalize_feature_columns(feature_columns)
    if training_columns != normalized_columns:
        raise RuntimeError("训练与评分的 Alpha158 特征列或顺序不一致")
    _atomic_write_json(
        candidate_metadata,
        {
            "schema_version": 1,
            "status": "candidate",
            "strategy_id": C.STRATEGY_ID,
            "release_id": window.release_id,
            "source_git_commit": source_git_commit,
            "config_sha256": config_sha256(),
            "runtime_code_sha256": runtime_code_sha256(),
            "model": {
                "path": "model.txt",
                "format": "lightgbm_native_booster",
                "sha256": _file_sha256(candidate_model),
                "best_iteration": int(booster.best_iteration),
            },
            "feature_schema": {
                "columns": normalized_columns,
                "count": len(normalized_columns),
                "sha256": feature_columns_sha256(normalized_columns),
            },
            "rolling_window": window.as_dict(),
            "row_counts": {"train": len(train), "validation": len(validation)},
        },
    )
    log(f"[candidate] 候选已保存 {candidate_model}；尚未回测批准，影子/QMT不会加载")
    return target


def _main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify", help="校验某日应使用的正式季度发布")
    verify.add_argument("date")
    train = subparsers.add_parser("train-candidate", help="显式训练候选，不会直接发布")
    train.add_argument("date")
    train.add_argument("--output-dir", type=Path)
    train.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "verify":
        published = validate_release(args.date)
        print(f"{published.release_id}: {published.model_path}")
    else:
        train_candidate(args.date, output_dir=args.output_dir, overwrite=args.overwrite)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

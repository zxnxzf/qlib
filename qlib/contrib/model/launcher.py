# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from ruamel.yaml import YAML


def _load_yaml(path: Path) -> dict:
    yaml = YAML(typ="safe", pure=True)
    with path.open("r", encoding="utf-8") as fp:
        return yaml.load(fp) or {}


def _dump_yaml(data: dict, path: Path) -> None:
    yaml = YAML()
    yaml.default_flow_style = False
    with path.open("w", encoding="utf-8") as fp:
        yaml.dump(data, fp)


def _resolve_workflow_config_path(path_str: str) -> Path:
    raw_path = Path(path_str).expanduser()
    if raw_path.is_absolute() and raw_path.exists():
        return raw_path

    repo_root = Path(__file__).resolve().parents[3]
    candidates: Iterable[Path] = [
        Path.cwd() / raw_path,
        repo_root / raw_path,
        repo_root / "examples" / raw_path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"workflow_config not found: {path_str}. Tried: "
        f"{', '.join(str(p) for p in [*candidates])}"
    )


def _ensure_dict(val: Any, *, name: str) -> Dict[str, Any]:
    if val is None:
        return {}
    if not isinstance(val, dict):
        raise TypeError(f"{name} must be a dict, got {type(val).__name__}")
    return val


def _write_tuner_artifacts(ex_dir: Path, run_id: str, analysis_df: Any, performance: Optional[dict] = None) -> None:
    ex_dir.mkdir(parents=True, exist_ok=True)

    exp_info_path = ex_dir / "exp_info.json"
    with exp_info_path.open("w", encoding="utf-8") as fp:
        exp_info = {"id": run_id}
        if performance:
            exp_info["performance"] = performance
        json.dump(exp_info, fp)

    analysis_dir = ex_dir / "sacred" / run_id
    analysis_dir.mkdir(parents=True, exist_ok=True)
    with (analysis_dir / "analysis.pkl").open("wb") as fp:
        pickle.dump(analysis_df, fp, protocol=pickle.HIGHEST_PROTOCOL)


def run(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="estimator")
    parser.add_argument(
        "-c",
        "--config_path",
        required=True,
        type=str,
        help="Path to tuner-generated estimator_config.yaml",
    )
    args = parser.parse_args(argv)

    estimator_cfg_path = Path(args.config_path).expanduser().resolve()
    estimator_cfg = _load_yaml(estimator_cfg_path)

    workflow_config = estimator_cfg.get("workflow_config")
    if not workflow_config:
        raise ValueError(
            "Missing `workflow_config` in estimator config. "
            "Please set it in your tuner_pipeline item, e.g. "
            "`workflow_config: benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_2020_2025.yaml`."
        )

    exp_cfg = _ensure_dict(estimator_cfg.get("experiment"), name="experiment")
    exp_dir = exp_cfg.get("dir")
    exp_name = exp_cfg.get("name")
    if not exp_dir or not exp_name:
        raise ValueError("Missing `experiment.dir` or `experiment.name` in estimator config.")

    ex_dir = (Path(exp_dir).expanduser() / exp_name).resolve()
    ex_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(str(ex_dir))

    workflow_path = _resolve_workflow_config_path(str(workflow_config))
    workflow_cfg = _load_yaml(workflow_path)

    model_args = _ensure_dict(_ensure_dict(estimator_cfg.get("model"), name="model").get("args"), name="model.args")
    strategy_args = _ensure_dict(
        _ensure_dict(estimator_cfg.get("strategy"), name="strategy").get("args"),
        name="strategy.args",
    )

    workflow_cfg.setdefault("task", {}).setdefault("model", {}).setdefault("kwargs", {}).update(model_args)
    workflow_cfg.setdefault("port_analysis_config", {}).setdefault("strategy", {}).setdefault("kwargs", {}).update(
        strategy_args
    )

    tuned_workflow_path = ex_dir / "workflow_config_tuned.yaml"
    _dump_yaml(workflow_cfg, tuned_workflow_path)

    from qlib.cli.run import workflow as qrun_workflow  # pylint: disable=C0415

    recorder = qrun_workflow(
        str(tuned_workflow_path),
        experiment_name=exp_name,
        uri_folder="mlruns",
        return_recorder=True,
    )

    run_id = str(recorder.info["id"])
    from qlib.utils.exceptions import LoadObjectError  # pylint: disable=C0415

    analysis_df = None
    for obj_name in ("portfolio_analysis/port_analysis_1day.pkl", "port_analysis_1day.pkl"):
        try:
            analysis_df = recorder.load_object(obj_name)
            break
        except LoadObjectError:
            continue
    if analysis_df is None:
        raise FileNotFoundError("port_analysis_1day.pkl not found in recorder artifacts.")

    performance = {}
    try:
        metrics = recorder.list_metrics()
    except Exception:  # pylint: disable=broad-exception-caught
        metrics = {}

    def _as_float(val: Any) -> Optional[float]:
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    ic_val = _as_float(metrics.get("IC"))
    if ic_val is not None:
        performance["model_pearsonr"] = ic_val
        performance["model_score"] = ic_val

    _write_tuner_artifacts(ex_dir, run_id, analysis_df, performance=performance)
    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except SystemExit:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[estimator] failed: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

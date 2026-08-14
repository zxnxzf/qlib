"""T-1 不可变信号包的构建与持久化。"""

import hashlib
import json
import math
import os
import re
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping, Optional

import pandas as pd

from .trade_planner import SignalCandidate, SignalPackage


SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = {1, 2}
REQUIRED_PARAMS = ("topk", "candidate_limit", "n_drop", "risk_degree", "lot")
PUBLISHED_PROVENANCE_FIELDS = (
    "strategy_id",
    "release_id",
    "model_sha256",
    "config_sha256",
    "runtime_code_sha256",
    "source_git_commit",
)


def _validate_dates(signal_date: str, exec_date: str) -> None:
    try:
        signal_day = date.fromisoformat(signal_date)
        exec_day = date.fromisoformat(exec_date)
    except ValueError as exc:
        raise ValueError("信号日或执行日格式无效") from exc
    if signal_day >= exec_day:
        raise ValueError(f"信号日必须早于执行日: {signal_date} >= {exec_date}")


def _validate_params(params: Mapping[str, float]) -> None:
    missing = [key for key in REQUIRED_PARAMS if key not in params]
    if missing:
        raise ValueError(f"信号包缺少策略参数: {', '.join(missing)}")
    if int(params["candidate_limit"]) != 100:
        raise ValueError("候选队列必须固定为 Top100")
    if int(params["topk"]) <= 0 or int(params["lot"]) <= 0:
        raise ValueError("topk 和 lot 必须为正数")
    if int(params["n_drop"]) < 0 or not 0 < float(params["risk_degree"]) <= 1:
        raise ValueError("n_drop 或 risk_degree 无效")


def _validate_provenance(provenance: Mapping[str, str]) -> None:
    if provenance.get("source_type") == "published_model":
        missing = [key for key in PUBLISHED_PROVENANCE_FIELDS if not provenance.get(key)]
        if missing:
            raise ValueError(f"信号包缺少正式发布来源: {', '.join(missing)}")
        for key in ("model_sha256", "config_sha256", "runtime_code_sha256"):
            if re.fullmatch(r"[0-9a-f]{64}", str(provenance[key])) is None:
                raise ValueError(f"信号包 {key} 无效")
        if re.fullmatch(r"[0-9a-f]{7,40}", str(provenance["source_git_commit"])) is None:
            raise ValueError("信号包 source_git_commit 无效")
        return
    if provenance.get("source_type") == "archived_scores":
        required = ("strategy_id", "release_id", "score_sha256", "config_sha256", "runtime_code_sha256")
        missing = [key for key in required if not provenance.get(key)]
        if missing:
            raise ValueError(f"信号包缺少归档评分来源: {', '.join(missing)}")
        for key in ("score_sha256", "config_sha256", "runtime_code_sha256"):
            if re.fullmatch(r"[0-9a-f]{64}", str(provenance[key])) is None:
                raise ValueError(f"信号包 {key} 无效")
        return
    raise ValueError("信号包来源类型无效")


def _ranked_scores(scores: pd.Series) -> pd.DataFrame:
    if not isinstance(scores, pd.Series):
        raise TypeError("scores 必须是 pandas Series")
    frame = scores.rename("score").reset_index()
    frame.columns = ["code", "score"]
    frame["code"] = frame["code"].astype(str)
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")
    frame = frame.dropna(subset=["score"])
    if frame["code"].duplicated().any():
        raise ValueError("预测分数包含重复股票代码")
    return frame.sort_values(["score", "code"], ascending=[False, True], kind="mergesort")


def build_signal_package(
    scores: pd.Series,
    signal_date: str,
    exec_date: str,
    gate_on: bool,
    holding_codes: Iterable[str],
    params: dict,
    batch_id: str,
    reference_closes: Optional[Mapping[str, float]] = None,
    provenance: Optional[Mapping[str, str]] = None,
) -> SignalPackage:
    """将 T-1 分数、参考价和策略参数锁成确定性的 Top100 信号包。"""
    _validate_dates(signal_date, exec_date)
    _validate_params(params)
    if not batch_id:
        raise ValueError("batch_id 不能为空")

    ranked = _ranked_scores(scores)
    score_by_code = dict(zip(ranked["code"], ranked["score"]))
    closes = {str(code): value for code, value in (reference_closes or {}).items()}
    candidates = []
    for rank, row in enumerate(ranked.head(100).itertuples(index=False), start=1):
        close = closes.get(row.code)
        if close is None or not math.isfinite(float(close)) or float(close) <= 0:
            raise ValueError(f"候选缺少有效 T-1 参考收盘价: {row.code}")
        candidates.append(
            SignalCandidate(
                code=row.code,
                score=float(row.score),
                rank=rank,
                reference_close=float(close),
            )
        )

    holding_scores = {}
    for raw_code in holding_codes:
        code = str(raw_code)
        score = score_by_code.get(code)
        holding_scores[code] = None if score is None or pd.isna(score) else float(score)

    return SignalPackage(
        batch_id=str(batch_id),
        signal_date=signal_date,
        exec_date=exec_date,
        gate_on=bool(gate_on),
        candidates=tuple(candidates),
        holding_scores=holding_scores,
        params=dict(params),
        provenance=dict(provenance or {}),
    )


def _package_content(package: SignalPackage) -> dict:
    return {
        "batch_id": package.batch_id,
        "signal_date": package.signal_date,
        "exec_date": package.exec_date,
        "gate_on": package.gate_on,
        "candidates": [
            {
                "code": candidate.code,
                "score": candidate.score,
                "rank": candidate.rank,
                "reference_close": candidate.reference_close,
            }
            for candidate in package.candidates
        ],
        "holding_scores": dict(sorted(package.holding_scores.items())),
        "params": dict(sorted(package.params.items())),
        "provenance": dict(sorted(package.provenance.items())),
    }


def _canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _checksum(content: dict) -> str:
    return hashlib.sha256(_canonical_json(content).encode("utf-8")).hexdigest()


def save_signal_package(package: SignalPackage, root: Path) -> Path:
    """先完整写入临时文件，再原子替换执行日信号文件。"""
    _validate_dates(package.signal_date, package.exec_date)
    _validate_params(package.params)
    _validate_provenance(package.provenance)
    content = _package_content(package)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "checksum": _checksum(content),
        "content": content,
    }
    signals_dir = Path(root) / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    path = signals_dir / f"{package.exec_date}.json"
    temporary = signals_dir / f"{package.exec_date}.json.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return path


def load_signal_package(exec_date: str, root: Path) -> SignalPackage:
    path = Path(root) / "signals" / f"{exec_date}.json"
    if not path.exists():
        raise FileNotFoundError(f"信号包不存在: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    schema_version = payload.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError("信号包版本不受支持")
    content = payload.get("content")
    if not isinstance(content, dict) or payload.get("checksum") != _checksum(content):
        raise ValueError("信号包内容校验失败")
    if content.get("exec_date") != exec_date:
        raise ValueError(f"信号包执行日不一致: expected={exec_date}, actual={content.get('exec_date')}")

    _validate_dates(content["signal_date"], content["exec_date"])
    _validate_params(content["params"])
    provenance = content.get("provenance", {})
    if schema_version >= 2:
        _validate_provenance(provenance)
    candidates = tuple(SignalCandidate(**candidate) for candidate in content["candidates"])
    if [candidate.rank for candidate in candidates] != list(range(1, len(candidates) + 1)):
        raise ValueError("信号包候选排名不连续")
    return SignalPackage(
        batch_id=content["batch_id"],
        signal_date=content["signal_date"],
        exec_date=content["exec_date"],
        gate_on=bool(content["gate_on"]),
        candidates=candidates,
        holding_scores=content["holding_scores"],
        params=content["params"],
        provenance=provenance,
    )

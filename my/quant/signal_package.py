"""T-1 不可变信号包的构建与持久化。"""

import hashlib
import json
import math
import os
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping, Optional

import pandas as pd

from .trade_planner import SignalCandidate, SignalPackage


SCHEMA_VERSION = 1
REQUIRED_PARAMS = ("topk", "candidate_limit", "n_drop", "risk_degree", "lot")


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
    }


def _canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _checksum(content: dict) -> str:
    return hashlib.sha256(_canonical_json(content).encode("utf-8")).hexdigest()


def save_signal_package(package: SignalPackage, root: Path) -> Path:
    """先完整写入临时文件，再原子替换执行日信号文件。"""
    _validate_dates(package.signal_date, package.exec_date)
    _validate_params(package.params)
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
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("信号包版本不受支持")
    content = payload.get("content")
    if not isinstance(content, dict) or payload.get("checksum") != _checksum(content):
        raise ValueError("信号包内容校验失败")
    if content.get("exec_date") != exec_date:
        raise ValueError(f"信号包执行日不一致: expected={exec_date}, actual={content.get('exec_date')}")

    _validate_dates(content["signal_date"], content["exec_date"])
    _validate_params(content["params"])
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
    )

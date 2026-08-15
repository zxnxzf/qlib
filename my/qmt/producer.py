"""Windows production Qlib entry point for immutable QMT signal files."""

from __future__ import annotations

import argparse
import math
import re
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence

import pandas as pd

from my.quant import config as C
from my.quant import data, gate, signal_
from my.quant.signal_package import build_signal_package

from . import PLANNER_VERSION
from .protocol import read_signal, signal_path, write_signal


SHANGHAI_TZ = timezone(timedelta(hours=8))
DEFAULT_RUNTIME_ROOT = C.REPO / "my" / "runtime"
_ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _aware_shanghai(value: Optional[datetime] = None) -> datetime:
    value = value or datetime.now(SHANGHAI_TZ)
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ)


def _planner_params() -> dict:
    return {
        "topk": C.TOPK,
        "candidate_limit": C.CANDIDATE_LIMIT,
        "n_drop": C.N_DROP,
        "hold_thresh": C.HOLD_THRESH,
        "risk_degree": C.RISK_DEGREE,
        "lot": C.LOT,
        "open_cost": C.OPEN_COST,
        "close_cost": C.CLOSE_COST,
        "min_cost": C.MIN_COST,
        "max_slippage": C.MAX_SLIPPAGE,
        "wait_seconds": C.EXECUTION_WAIT_SECONDS,
    }


def _ranked_scores(scores: pd.Series) -> pd.Series:
    if not isinstance(scores, pd.Series):
        raise TypeError("scores must be a pandas Series")
    frame = scores.rename("score").reset_index()
    frame.columns = ["code", "score"]
    frame["code"] = frame["code"].astype(str)
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")
    frame = frame.dropna(subset=["score"])
    frame = frame[frame["score"].map(math.isfinite)]
    if frame["code"].duplicated().any():
        raise ValueError("prediction scores contain duplicate stock codes")
    frame = frame.sort_values(["score", "code"], ascending=[False, True], kind="mergesort")
    return frame.set_index("code")["score"]


def _reference_closes(codes: Iterable[str], signal_date: str) -> Dict[str, float]:
    required = [str(code) for code in codes]
    bars = data.day_bars(signal_date, fields=("$close", "$factor"))
    closes: Dict[str, float] = {}
    for code in required:
        if code not in bars.index:
            continue
        row = bars.loc[code]
        close, factor = row["close"], row["factor"]
        if pd.isna(close):
            continue
        raw = float(close) / float(factor) if pd.notna(factor) and float(factor) > 0 else float(close)
        if math.isfinite(raw) and raw > 0:
            closes[code] = raw
    missing = [code for code in required if code not in closes]
    if missing:
        closes.update(data.raw_closes_asof(missing, signal_date))
    return closes


def _validate_account_alias(account_alias: str) -> str:
    value = str(account_alias).strip()
    if _ALIAS_PATTERN.fullmatch(value) is None:
        raise ValueError("account_alias must contain only letters, digits, '_' or '-'")
    return value


def build_payload(
    signal_date: str,
    exec_date: str,
    account_alias: str,
    scores: pd.Series,
    gate_on: bool,
    gate_note: str,
    provenance: dict,
    release_id: str,
    data_asof: str,
    now: Optional[datetime] = None,
    reference_closes: Optional[Dict[str, float]] = None,
) -> dict:
    """Build a protocol-v2 signal without writing mutable QMT state."""
    account_alias = _validate_account_alias(account_alias)
    if not isinstance(gate_on, bool):
        raise TypeError("gate_on must be bool")
    if data_asof != signal_date:
        raise ValueError(f"QMT production data_asof must equal signal_date: {data_asof} != {signal_date}")
    ranked = _ranked_scores(scores)
    top_codes = list(ranked.head(C.CANDIDATE_LIMIT).index)
    closes = reference_closes if reference_closes is not None else _reference_closes(top_codes, signal_date)
    params = _planner_params()
    batch_id = f"{signal_date}_{exec_date}_{release_id}"
    package = build_signal_package(
        scores=ranked,
        signal_date=signal_date,
        exec_date=exec_date,
        gate_on=gate_on,
        holding_codes=(),
        params=params,
        batch_id=batch_id,
        reference_closes=closes,
        provenance=provenance,
    )
    created_at = _aware_shanghai(now)
    # 09:30-09:31 includes bars stamped during the whole 09:31 minute.
    expires_at = datetime.combine(pd.Timestamp(exec_date).date(), time(9, 32), tzinfo=SHANGHAI_TZ)
    if created_at >= expires_at:
        raise ValueError("signal creation time is already past the execution deadline")
    return {
        "schema_version": 2,
        "batch_id": batch_id,
        "signal_date": signal_date,
        "exec_date": exec_date,
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "account_alias": account_alias,
        "data_asof": data_asof,
        "provenance": dict(provenance),
        "planner_version": PLANNER_VERSION,
        "gate": {
            "on": gate_on,
            "index": C.GATE_INDEX,
            "ma_window": C.GATE_MA,
            "confirm_days": C.GATE_CONFIRM_DAYS,
            "surge_reentry": C.GATE_SURGE_REENTRY,
            "note": str(gate_note),
        },
        "params": params,
        "scores": {str(code): float(score) for code, score in ranked.sort_index().items()},
        "candidates": [
            {
                "rank": candidate.rank,
                "code": candidate.code,
                "score": candidate.score,
                "reference_close": candidate.reference_close,
            }
            for candidate in package.candidates
        ],
    }


def generate_signal(
    signal_date: Optional[str] = None,
    account_alias: str = "qmt_sim",
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
    skip_update: bool = False,
    now: Optional[datetime] = None,
    log=print,
) -> Path:
    """Validate the published strategy and atomically publish one T signal."""
    now = _aware_shanghai(now)
    if not skip_update:
        updated = data.update_data()
        log(f"[qmt-producer] data update {'succeeded' if updated else 'did not publish a new package'}")
    requested = signal_date or data.expected_signal_date(now)
    latest = data.latest_data_date()
    if latest != requested:
        raise RuntimeError(f"QMT production data must end exactly on signal date: latest={latest}, signal={requested}")
    exec_date = data.next_trade_date(requested)
    if exec_date is None:
        raise RuntimeError(f"no next trading day after {requested}")

    path = signal_path(Path(runtime_root), exec_date)
    if path.exists():
        existing = read_signal(
            Path(runtime_root),
            exec_date,
            expected_account_alias=_validate_account_alias(account_alias),
            expected_planner_version=PLANNER_VERSION,
            now=now,
        )
        if existing.get("signal_date") != requested:
            raise RuntimeError("existing signal belongs to another signal date")
        log(f"[qmt-producer] existing immutable signal reused: {path}")
        return path

    published = signal_.validate_release(requested)
    scores = signal_.scores_for(requested, log=log, published=published)
    gate_on, gate_note = gate.gate_for_next_day(requested)
    ranked = _ranked_scores(scores)
    closes = _reference_closes(ranked.head(C.CANDIDATE_LIMIT).index, requested)
    payload = build_payload(
        signal_date=requested,
        exec_date=exec_date,
        account_alias=account_alias,
        scores=ranked,
        gate_on=gate_on,
        gate_note=gate_note,
        provenance=signal_.release_provenance(published),
        release_id=published.release_id,
        data_asof=latest,
        now=now,
        reference_closes=closes,
    )
    written = write_signal(Path(runtime_root), payload)
    log(
        f"[qmt-producer] wrote {written} gate={'on' if gate_on else 'off'} "
        f"scores={len(payload['scores'])} candidates={len(payload['candidates'])}"
    )
    return written


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate an immutable Qlib -> standard QMT signal")
    parser.add_argument("--signal-date", help="T-1 in YYYY-MM-DD; defaults to the expected latest signal day")
    parser.add_argument("--account-alias", default="qmt_sim", help="local alias only; never a broker account id")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--skip-update", action="store_true", help="skip download but keep the strict freshness check")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    path = generate_signal(
        signal_date=args.signal_date,
        account_alias=args.account_alias,
        runtime_root=args.runtime_root,
        skip_update=args.skip_update,
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

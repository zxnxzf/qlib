#!/usr/bin/env python3
"""用同一份历史预测对比影子回放与 Qlib 普通回测。"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from my.quant import config as C
from my.quant import data, gate
from my.quant.parity import (
    MarketCache,
    compare_snapshots,
    run_qlib_backtest,
    run_shadow_replay,
    validate_snapshot_dates,
    write_parity_artifacts,
)


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2025-01-02")
    parser.add_argument("--end", default="2026-07-28")
    parser.add_argument("--warmup", default="2024-12-31")
    parser.add_argument(
        "--pred",
        type=Path,
        default=C.ARTIFACTS / "candidate1_pred.pkl",
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--verbose-shadow", action="store_true")
    return parser.parse_args()


def _load_prediction(path: Path) -> pd.Series:
    pred = pd.read_pickle(path)
    if isinstance(pred, pd.DataFrame):
        if pred.shape[1] != 1:
            raise ValueError(f"预测必须只有一列，实际为 {pred.shape[1]} 列")
        pred = pred.iloc[:, 0]
    if not isinstance(pred.index, pd.MultiIndex) or "datetime" not in pred.index.names:
        raise ValueError("预测必须使用含 datetime 的 MultiIndex")
    pred = pred.copy()
    pred.name = "score"
    return pred


def _build_execution_gate(start: str, end: str) -> pd.Series:
    history_start = (pd.Timestamp(start) - pd.Timedelta(days=C.GATE_MA * 5)).strftime(
        "%Y-%m-%d"
    )
    next_day = data.next_trade_date(end) or end
    closes = data.index_closes(C.GATE_INDEX, history_start, next_day)
    execution_gate = gate.gate_series(closes).shift(1, fill_value=True).astype(bool)
    execution_gate.index = pd.to_datetime(execution_gate.index).normalize()
    return execution_gate


def main():
    args = _parse_args()
    if not (args.warmup < args.start <= args.end):
        raise ValueError("日期必须满足 warmup < start <= end")

    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_dir = args.output_dir or (
        C.ARTIFACTS / "shadow_backtest_parity" / run_id
    )
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"输出目录必须为空: {output_dir}")
    state_dir = C.STATE_DIR / "backfills" / f"parity_{run_id}"

    print(f"[parity] 初始化 Qlib，区间 {args.start} ~ {args.end}", flush=True)
    data.init_qlib(kernels=4)
    pred = _load_prediction(args.pred)
    execution_gate = _build_execution_gate(args.start, args.end)

    print("[parity] 批量加载回放行情…", flush=True)
    cache = MarketCache.from_qlib(args.warmup, args.end)

    print("[parity] 运行 Qlib 普通回测…", flush=True)
    qlib_snapshots = run_qlib_backtest(
        pred,
        execution_gate,
        args.start,
        args.end,
        factor_cache=cache,
    )

    print("[parity] 运行影子模式历史回放…", flush=True)
    shadow_log = print if args.verbose_shadow else (lambda _message: None)
    shadow_snapshots = run_shadow_replay(
        pred=pred,
        gate_by_exec_date=execution_gate,
        cache=cache,
        warmup=args.warmup,
        start=args.start,
        end=args.end,
        state_dir=state_dir,
        log=shadow_log,
    )

    expected_dates = [
        day for day in data.calendar() if args.start <= day <= args.end
    ]
    validate_snapshot_dates(qlib_snapshots, expected_dates)
    validate_snapshot_dates(shadow_snapshots, expected_dates)
    result = compare_snapshots(qlib_snapshots, shadow_snapshots)
    metadata = {
        "start": args.start,
        "end": args.end,
        "warmup": args.warmup,
        "prediction": str(args.pred.resolve()),
        "initial_cash": C.SHADOW_INIT_CASH,
        "gate_index": C.GATE_INDEX,
        "topk": C.TOPK,
        "n_drop": C.N_DROP,
        "only_tradable_qlib": True,
        "shadow_state_dir": str(state_dir),
    }
    write_parity_artifacts(result, output_dir, metadata)
    print(
        f"[parity] 完成：Qlib={result.summary['qlib_final_nav']:.2f}，"
        f"影子={result.summary['shadow_final_nav']:.2f}，"
        f"持仓匹配率={result.summary['holding_match_rate']:.2%}，"
        f"订单匹配率={result.summary['order_match_rate']:.2%}",
        flush=True,
    )
    print(f"[parity] 报告: {output_dir / 'report.md'}", flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# 注意：本脚本为 Docker 时代遗留（/qlib/ 硬路径），在本机不可直接运行；可用版本见 my/scripts/compare_live_chain_local.py
"""
Compare live (re-predict) manual_daily_trade vs workflow positions for a single day.

Usage:
  1) Edit TRADE_DATE and RUN_ID below.
  2) Run: python /qlib/examples/custom/compare_live_day.py
  3) Check output_dir for:
     - positions_manual_<DATE>.csv
     - positions_workflow_<DATE>.csv
     - positions_diff_<DATE>.csv
     - compare.log
"""

from __future__ import annotations

import copy
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Dict

import pandas as pd

import qlib
from qlib.data import D

import sys

# Ensure local repo import precedence.
sys.path.insert(0, "/qlib/examples/custom")
import manual_daily_trade as mdt  # noqa: E402


# ======= EDIT THESE =======
TRADE_DATE = "2025-01-03"
RUN_ID = "543191cf7de747f8b9a7a8e1006fcbb0"
EXPERIMENT_ID = "1"
MLRUNS_URI = "/qlib/examples/mlruns"
INITIAL_CASH = 50000.0
# "hold" keeps positions if pred_date has no predictions; set to "exit" for strict live behavior.
EMPTY_PRED_ACTION = "hold"
# ==========================


def _get_factor_map(codes, date_str: str) -> Dict[str, float]:
    if not codes:
        return {}
    try:
        df = D.features(codes, ["$factor"], start_time=date_str, end_time=date_str)
    except Exception:
        df = pd.DataFrame()
    if df is None or df.empty:
        return {code: 1.0 for code in codes}
    factor = df.reset_index().rename(columns={"$factor": "factor"})
    factor = factor.dropna(subset=["factor"])
    return {str(row["instrument"]): float(row["factor"]) for _, row in factor.iterrows()}


def _write_positions_csv(path: Path, holdings: Dict[str, float], cash: float) -> None:
    rows = [{"code": "CASH", "position": float(cash)}]
    for code in sorted(holdings):
        amt = holdings[code]
        if float(amt).is_integer():
            amt = int(amt)
        rows.append({"code": code, "position": amt})
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def _read_positions(path: Path) -> tuple[Dict[str, float], float]:
    df = pd.read_csv(path)
    holdings: Dict[str, float] = {}
    cash = 0.0
    for _, row in df.iterrows():
        code = str(row["code"])
        pos = float(row["position"])
        if code == "CASH":
            cash = pos
        else:
            holdings[code] = pos
    return holdings, cash


def main() -> None:
    output_dir = Path(f"/qlib/examples/custom/compare_live_day_{TRADE_DATE}")
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "compare.log"

    positions_path = output_dir / "positions_manual.csv"
    positions_next_path = output_dir / "positions_manual_next.csv"
    orders_out_path = output_dir / "orders_manual.csv"
    history_path = output_dir / "holdings_history_manual.json"

    positions_path.write_text(f"code,position\nCASH,{INITIAL_CASH}\n", encoding="utf-8")
    history_path.write_text("{}", encoding="utf-8")

    cfg = copy.deepcopy(mdt.DEFAULT_CONFIG)
    cfg["paths"]["positions"] = str(positions_path)
    cfg["paths"]["positions_next"] = str(positions_next_path)
    cfg["paths"]["orders_out"] = str(orders_out_path)
    cfg["paths"]["holdings_history"] = str(history_path)
    cfg["paths"]["mlruns_uri"] = MLRUNS_URI

    cfg["prediction"]["experiment_id"] = EXPERIMENT_ID
    cfg["prediction"]["recorder_id"] = RUN_ID
    cfg["prediction"]["empty_pred_action"] = EMPTY_PRED_ACTION

    cfg["workflow_alignment"]["enabled"] = True
    cfg["workflow_alignment"]["mode"] = "live"
    cfg["workflow_alignment"]["use_required_pred_date"] = True
    cfg["workflow_alignment"]["use_position_count"] = False
    cfg["workflow_alignment"]["use_recorder_pred"] = False
    cfg["workflow_alignment"]["use_backtest_window"] = False
    cfg["workflow_alignment"]["use_execution_simulator"] = True

    cfg["data_update"]["enable_auto_update"] = False

    # Inject config into manual_daily_trade module so it uses our paths.
    mdt.DEFAULT_CONFIG = cfg

    artifacts = Path(MLRUNS_URI) / EXPERIMENT_ID / RUN_ID / "artifacts"
    positions_dict = pd.read_pickle(artifacts / "portfolio_analysis" / "positions_normal_1day.pkl")

    qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region="cn")

    with log_path.open("w", encoding="utf-8") as log, redirect_stdout(log), redirect_stderr(log):
        def log_print(*args):
            print(" ".join(str(a) for a in args), flush=True)

        log_print("=== compare manual(live) vs workflow (from cash, re-predict) ===")
        log_print("run_id:", RUN_ID)
        log_print("artifacts:", artifacts)
        log_print("output_dir:", output_dir)
        log_print("trade_date:", TRADE_DATE)
        log_print("\n" + "=" * 60)
        log_print("[DATE]", TRADE_DATE)

        rc = mdt.main(trade_date_override=TRADE_DATE)
        log_print("[OK] manual_daily_trade exit code:", rc)

        if not positions_next_path.exists():
            log_print("[WARN] manual positions not found:", positions_next_path)
            return

        manual_holdings, manual_cash = _read_positions(positions_next_path)
        manual_out = output_dir / f"positions_manual_{TRADE_DATE}.csv"
        _write_positions_csv(manual_out, manual_holdings, manual_cash)

        ts = pd.Timestamp(TRADE_DATE)
        if ts not in positions_dict:
            log_print("[WARN] workflow positions missing date", TRADE_DATE)
            return
        position = positions_dict[ts]
        cash = float(position.get_cash())
        stock_list = position.get_stock_list()
        factor_map = _get_factor_map(stock_list, TRADE_DATE)
        holdings = {}
        for code in stock_list:
            amount_adj = float(position.get_stock_amount(code))
            factor = factor_map.get(code, 1.0)
            raw = int(amount_adj * factor)
            holdings[code] = raw

        workflow_out = output_dir / f"positions_workflow_{TRADE_DATE}.csv"
        _write_positions_csv(workflow_out, holdings, cash)

        all_codes = set(manual_holdings) | set(holdings)
        diff_rows = []
        for code in sorted(all_codes):
            m = manual_holdings.get(code, 0.0)
            w = holdings.get(code, 0.0)
            if m == w:
                continue
            diff_rows.append({"code": code, "manual": m, "workflow": w, "delta": m - w})
        diff_rows.sort(key=lambda x: abs(x["delta"]), reverse=True)
        diff_path = output_dir / f"positions_diff_{TRADE_DATE}.csv"
        pd.DataFrame(diff_rows).to_csv(diff_path, index=False, encoding="utf-8-sig")

        cash_delta = manual_cash - cash
        max_abs = max([abs(r["delta"]) for r in diff_rows], default=0.0)
        log_print(f"[COMPARE] diff_count={len(diff_rows)}, max_abs_diff={max_abs}, cash_delta={cash_delta}")
        log_print("[DIFF TOP10]")
        for row in diff_rows[:10]:
            log_print(" ", row["code"], "manual=", row["manual"], "workflow=", row["workflow"], "delta=", row["delta"])

        log_print("\n[ALL DONE] compare finished, log:", log_path)


if __name__ == "__main__":
    main()

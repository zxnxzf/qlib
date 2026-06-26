#!/usr/bin/env python3
"""
Chain-compare manual live (re-predict) vs workflow positions across multiple dates.

Edit the constants below (TRADE_DATES, RUN_ID, etc.), then run:
  python /qlib/examples/custom/compare_live_chain.py

Outputs under:
  /qlib/examples/custom/compare_live_chain_<start>_<end>/
"""

from __future__ import annotations

import argparse
import copy
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Dict, List

import pandas as pd
import traceback

import qlib
from qlib.data import D

import sys

# Ensure local repo import precedence.
sys.path.insert(0, "/qlib/examples/custom")
import manual_daily_trade as mdt  # noqa: E402


# ======= EDIT THESE =======
TRADE_DATES = ["2025-01-03", "2025-01-06", "2025-01-07"]
RUN_ID = "543191cf7de747f8b9a7a8e1006fcbb0"
EXPERIMENT_ID = "1"
MLRUNS_URI = "/qlib/examples/mlruns"
INITIAL_CASH = 50000.0
# "hold" keeps positions if pred_date has no predictions (to allow day 1 compare)
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


def _resolve_trade_dates(start: str | None, end: str | None) -> List[str]:
    if start and end:
        cal = D.calendar(start_time=start, end_time=end)
        return [d.strftime("%Y-%m-%d") for d in cal]
    if start or end:
        raise ValueError("start/end must be provided together")
    return list(TRADE_DATES)


def main() -> None:
    parser = argparse.ArgumentParser(description="Chain-compare live vs workflow over a date range.")
    parser.add_argument("--start", type=str, default=None, help="Start trade date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="End trade date (YYYY-MM-DD)")
    args = parser.parse_args()

    # Init qlib early so D.calendar works when using --start/--end.
    base_cfg = mdt.DEFAULT_CONFIG.get("qlib_init", {})
    qlib.init(
        provider_uri=base_cfg.get("provider_uri", "~/.qlib/qlib_data/cn_data"),
        region=base_cfg.get("region", "cn"),
    )

    trade_dates = _resolve_trade_dates(args.start, args.end)
    if not trade_dates:
        raise ValueError("TRADE_DATES is empty")
    output_dir = Path(
        f"/qlib/examples/custom/compare_live_chain_{trade_dates[0]}_{trade_dates[-1]}"
    )
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

        try:
            log_print("=== compare manual(live) vs workflow (from cash, re-predict, chain) ===")
            log_print("run_id:", RUN_ID)
            log_print("artifacts:", artifacts)
            log_print("output_dir:", output_dir)
            log_print("trade_dates:", trade_dates)

            for date_str in trade_dates:
                log_print("\n" + "=" * 60)
                log_print("[DATE]", date_str)

                try:
                    rc = mdt.main(trade_date_override=date_str)
                    log_print("[OK] manual_daily_trade exit code:", rc)
                except Exception:
                    log_print("[ERROR] manual_daily_trade crashed")
                    traceback.print_exc()
                    break

                if not positions_next_path.exists():
                    log_print("[WARN] manual positions not found:", positions_next_path)
                    break

                manual_holdings, manual_cash = _read_positions(positions_next_path)
                manual_out = output_dir / f"positions_manual_{date_str}.csv"
                _write_positions_csv(manual_out, manual_holdings, manual_cash)

                ts = pd.Timestamp(date_str)
                if ts not in positions_dict:
                    log_print("[WARN] workflow positions missing date", date_str)
                    break
                position = positions_dict[ts]
                cash = float(position.get_cash())
                stock_list = position.get_stock_list()
                factor_map = _get_factor_map(stock_list, date_str)
                holdings = {}
                for code in stock_list:
                    amount_adj = float(position.get_stock_amount(code))
                    factor = factor_map.get(code, 1.0)
                    raw = int(amount_adj * factor)
                    holdings[code] = raw
                workflow_out = output_dir / f"positions_workflow_{date_str}.csv"
                _write_positions_csv(workflow_out, holdings, cash)

                all_codes = set(manual_holdings) | set(holdings)
                diff_rows: List[Dict[str, float]] = []
                for code in sorted(all_codes):
                    m = manual_holdings.get(code, 0.0)
                    w = holdings.get(code, 0.0)
                    if m == w:
                        continue
                    diff_rows.append({"code": code, "manual": m, "workflow": w, "delta": m - w})
                diff_rows.sort(key=lambda x: abs(x["delta"]), reverse=True)
                diff_path = output_dir / f"positions_diff_{date_str}.csv"
                pd.DataFrame(diff_rows).to_csv(diff_path, index=False, encoding="utf-8-sig")

                cash_delta = manual_cash - cash
                max_abs = max([abs(r["delta"]) for r in diff_rows], default=0.0)
                log_print(f"[COMPARE] diff_count={len(diff_rows)}, max_abs_diff={max_abs}, cash_delta={cash_delta}")
                log_print("[DIFF TOP10]")
                for row in diff_rows[:10]:
                    log_print(
                        " ",
                        row["code"],
                        "manual=",
                        row["manual"],
                        "workflow=",
                        row["workflow"],
                        "delta=",
                        row["delta"],
                    )

                # Chain: use today's output as next day's input
                positions_path.write_text(manual_out.read_text(encoding="utf-8-sig"), encoding="utf-8")
                log_print("[OK] chained positions:", manual_out, "->", positions_path)

            log_print("\n[ALL DONE] compare finished, log:", log_path)
        except Exception:
            log_print("[FATAL] compare_live_chain crashed")
            traceback.print_exc()


if __name__ == "__main__":
    main()

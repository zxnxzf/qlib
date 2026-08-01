#!/usr/bin/env python3
"""
本地环境版链式对比：manual_daily_trade（live 模式重预测）vs qrun LightGBM workflow 回测持仓。

适配自 examples/custom/compare_live_chain.py，改动：
- mlruns/recorder 指向本地 my/mlruns 的 Alpha158+CSI300+LightGBM 实验（回测 2017-2020）
- workflow yaml 指向 run-configs/lightgbm_alpha158_local.yaml
- 策略参数对齐该 yaml：topk=50, n_drop=5，其余用 TopkDropoutStrategy 类默认（hold_thresh=1 等）
- 对比日期取回测窗口最初几个交易日（workflow 首日 2017-01-03 无信号空仓，
  故从 2017-01-04 起双方都从 1 亿现金开始首次建仓，严格可比）
"""

import copy
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Dict, List

import pandas as pd

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parents[1]
for p in (str(_REPO_ROOT), str(_REPO_ROOT / "my" / "trading"), str(_REPO_ROOT / "my" / "trading")):
    if p not in sys.path:
        sys.path.insert(0, p)

import qlib  # noqa: E402
from qlib.data import D  # noqa: E402
import manual_daily_trade as mdt  # noqa: E402

TRADE_DATES = ["2017-01-04", "2017-01-05", "2017-01-06"]
RUN_ID = "32fb882581004afa9ac2e9c5e95123db"
EXPERIMENT_ID = "583100969444960600"
MLRUNS_URI = "/Users/bytedance/code/qlib/my/mlruns"
WORKFLOW_YAML = "/Users/bytedance/code/qlib/run-configs/lightgbm_alpha158_local.yaml"
PROVIDER_URI = "/Users/bytedance/code/qlib/my/data/cn_data"
INITIAL_CASH = 100000000.0
EMPTY_PRED_ACTION = "hold"


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


def _read_positions(path: Path):
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
    output_dir = _THIS_DIR / f"compare_local_{TRADE_DATES[0]}_{TRADE_DATES[-1]}"
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "compare.log"

    positions_path = output_dir / "positions_manual.csv"
    positions_next_path = output_dir / "positions_manual_next.csv"
    orders_out_path = output_dir / "orders_manual.csv"
    history_path = output_dir / "holdings_history_manual.json"
    pnl_path = output_dir / "pnl_history.csv"

    positions_path.write_text(f"code,position\nCASH,{INITIAL_CASH}\n", encoding="utf-8")
    history_path.write_text("{}", encoding="utf-8")

    cfg = copy.deepcopy(mdt.DEFAULT_CONFIG)
    cfg["paths"]["state_dir"] = str(output_dir)
    cfg["paths"]["positions"] = str(positions_path)
    cfg["paths"]["positions_next"] = str(positions_next_path)
    cfg["paths"]["orders_out"] = str(orders_out_path)
    cfg["paths"]["holdings_history"] = str(history_path)
    cfg["paths"]["pnl_history"] = str(pnl_path)
    cfg["paths"]["mlruns_uri"] = MLRUNS_URI

    cfg["qlib_init"]["provider_uri"] = PROVIDER_URI
    cfg["qlib_init"]["kernels"] = 8

    cfg["prediction"]["experiment_id"] = EXPERIMENT_ID
    cfg["prediction"]["recorder_id"] = RUN_ID
    cfg["prediction"]["empty_pred_action"] = EMPTY_PRED_ACTION
    cfg["prediction"]["top_k"] = 50
    cfg["prediction"]["provider_uri"] = PROVIDER_URI

    cfg["workflow_alignment"]["enabled"] = True
    cfg["workflow_alignment"]["mode"] = "live"
    cfg["workflow_alignment"]["workflow_config_path"] = WORKFLOW_YAML
    cfg["workflow_alignment"]["handler"] = {}
    cfg["workflow_alignment"]["use_required_pred_date"] = True
    cfg["workflow_alignment"]["use_position_count"] = False
    cfg["workflow_alignment"]["use_recorder_pred"] = False
    cfg["workflow_alignment"]["use_backtest_window"] = False
    cfg["workflow_alignment"]["use_execution_simulator"] = True

    # 对齐 run-configs/lightgbm_alpha158_local.yaml 的策略参数（其余为类默认值）
    cfg["strategy"]["top_k"] = 50
    cfg["strategy"]["n_drop"] = 5
    cfg["strategy"]["hold_thresh"] = 1
    cfg["strategy"]["forbid_all_trade_at_limit"] = True
    cfg["trading"]["hold_thresh"] = 1

    cfg["data_update"]["enable_auto_update"] = False

    mdt.DEFAULT_CONFIG = cfg

    artifacts = Path(MLRUNS_URI) / EXPERIMENT_ID / RUN_ID / "artifacts"
    positions_dict = pd.read_pickle(artifacts / "portfolio_analysis" / "positions_normal_1day.pkl")

    qlib.init(provider_uri=PROVIDER_URI, region="cn")

    with log_path.open("w", encoding="utf-8") as log, redirect_stdout(log), redirect_stderr(log):
        def log_print(*args):
            print(" ".join(str(a) for a in args), flush=True)

        try:
            log_print("=== compare manual(live re-predict) vs qrun workflow, chained ===")
            log_print("run_id:", RUN_ID)
            log_print("workflow yaml:", WORKFLOW_YAML)
            log_print("trade_dates:", TRADE_DATES)

            for date_str in TRADE_DATES:
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
                log_print(
                    f"[COMPARE] holdings={len(manual_holdings)}/{len(holdings)} "
                    f"diff_count={len(diff_rows)}, max_abs_diff={max_abs}, cash_delta={cash_delta:.2f}"
                )
                for row in diff_rows[:10]:
                    log_print("  ", row["code"], "manual=", row["manual"], "workflow=", row["workflow"], "delta=", row["delta"])

                positions_path.write_text(manual_out.read_text(encoding="utf-8-sig"), encoding="utf-8")
                log_print("[OK] chained positions:", manual_out, "->", positions_path)

            log_print("\n[ALL DONE] compare finished")
        except Exception:
            log_print("[FATAL] compare crashed")
            traceback.print_exc()


if __name__ == "__main__":
    main()

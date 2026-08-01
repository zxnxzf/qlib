#!/usr/bin/env python3
"""实验：换手-alpha 前沿扫描（ndrop_frontier_open_exec）

假设：alpha 前置集中→每日新换入票贡献大头；适度调低 n_drop 能保住大部分 alpha、
换手/滑点成本近似线性下降，存在净超额优于 n_drop=10 的点。
口径：开盘执行 + impact_cost=0.001（现实滑点，压力测试后的建议口径）。
对照：n_drop=10 同口径（压力测试实测 净=-1.51%）。
"""

import sys
from pathlib import Path

sys.path.insert(0, "/Users/bytedance/code/qlib")
sys.path.insert(0, "/Users/bytedance/code/qlib/my/scripts")

import pandas as pd
import qlib
from qlib.contrib.evaluate import backtest_daily

ART = Path("/Users/bytedance/code/qlib/my/artifacts")
NAME = "ndrop_frontier_open_exec"
ANN = 238


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle(ART / "candidate1_pred.pkl")

    rows = {}
    for n_drop in (2, 5, 8):
        strategy = {
            "class": "TopkDropoutStrategy",
            "module_path": "qlib.contrib.strategy",
            "kwargs": {"signal": pred, "topk": 50, "n_drop": n_drop, "hold_thresh": 1, "only_tradable": True},
        }
        report, _ = backtest_daily(
            start_time="2023-01-01",
            end_time="2026-07-28",
            strategy=strategy,
            account=1_000_000,
            benchmark="SH000300",
            exchange_kwargs={
                "deal_price": "open",
                "limit_threshold": ("$open/Ref($close,1)-1 > 0.095", "$open/Ref($close,1)-1 < -0.095"),
                "open_cost": 0.0005,
                "close_cost": 0.0015,
                "min_cost": 5,
                "impact_cost": 0.001,
            },
        )
        gross = report["return"] - report["bench"]
        net = report["return"] - report["cost"] - report["bench"]
        ir = net.mean() / net.std() * ANN ** 0.5
        rows[n_drop] = report
        print(
            f"n_drop={n_drop}: 毛={gross.mean()*ANN:+.2%} 净={net.mean()*ANN:+.2%} IR={ir:+.2f} "
            f"换手={report['turnover'].mean():.1%}",
            flush=True,
        )
    print("对照 n_drop=10(同口径): 毛=+12.45% 净=-1.51% IR=-0.08 换手=41.4%", flush=True)

    best = max(rows, key=lambda k: (rows[k]["return"] - rows[k]["cost"] - rows[k]["bench"]).mean())
    rows[best].to_pickle(ART / f"exp_{NAME}_best_ndrop{best}_report.pkl")

    from exp_mlflow_log import log_experiment

    metrics = {}
    for k, r in rows.items():
        net = r["return"] - r["cost"] - r["bench"]
        metrics[f"net_ndrop{k}"] = net.mean() * ANN
    rid = log_experiment(
        NAME,
        params={"base": "open_exec_rolling", "impact_cost": 0.001, "sweep": "n_drop 2/5/8", "control": "n_drop=10"},
        metrics=metrics,
    )
    print(f"mlflow run: {rid} best_ndrop={best}", flush=True)


if __name__ == "__main__":
    main()

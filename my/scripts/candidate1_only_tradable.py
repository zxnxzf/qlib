#!/usr/bin/env python3
"""候选一号全期重测：only_tradable=True（卖出候选跳过不可交易死票，解除锁死）。"""

import sys

sys.path.insert(0, "/Users/bytedance/code/qlib")
import pandas as pd
import qlib
from qlib.contrib.evaluate import backtest_daily

ANN = 238


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle("/Users/bytedance/code/qlib/my/artifacts/candidate1_pred.pkl")
    strategy = {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy",
        "kwargs": {"signal": pred, "topk": 50, "n_drop": 10, "hold_thresh": 1, "only_tradable": True},
    }
    report, _ = backtest_daily(
        start_time="2023-01-01",
        end_time="2026-07-28",
        strategy=strategy,
        account=1_000_000,
        benchmark="SH000300",
        exchange_kwargs={
            "limit_threshold": 0.095,
            "deal_price": "close",
            "open_cost": 0.0005,
            "close_cost": 0.0015,
            "min_cost": 5,
        },
    )
    report.to_pickle("/Users/bytedance/code/qlib/my/artifacts/candidate1_report_tradable.pkl")
    ex = report["return"] - report["cost"] - report["bench"]

    def stat(s, label):
        ir = s.mean() / s.std() * ANN ** 0.5 if s.std() > 0 else float("nan")
        print(f"  {label:14s} 超额年化={s.mean()*ANN:+.2%} IR={ir:+.2f}")

    print("== 候选一号(only_tradable) 含成本超额 vs 沪深300 ==")
    stat(ex, "全期")
    for y, g in ex.groupby(ex.index.year):
        stat(g, f"  {y}年")
    stat(ex[ex.index >= "2025-08-01"], "  25-08后(已烧)")
    print(f"  逐年换手: " + "  ".join(f"{y}={g['turnover'].mean():.1%}" for y, g in report.groupby(report.index.year)))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""票池对比轮：全市场+过滤。复用 candidate1_pred（all_no_bj 滚动预测），
入组前剔除：20日均成交额截面后20% 或 未复权价<2元 的票。"""

import sys

sys.path.insert(0, "/Users/bytedance/code/qlib")
import pandas as pd
import qlib
from qlib.data import D
from qlib.contrib.evaluate import backtest_daily

ANN = 238


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle("/Users/bytedance/code/qlib/my/artifacts/candidate1_pred.pkl")

    inst = D.instruments("all_no_bj")
    feats = D.features(inst, ["Mean($amount, 20)", "$close/$factor"], start_time="2023-01-01", end_time="2026-07-28")
    feats.columns = ["amt20", "raw_close"]
    df = pred.join(feats, how="left")
    # 截面流动性分位（每天），避免 amount 单位不确定的问题
    df["amt_rank"] = df.groupby(level="datetime")["amt20"].rank(pct=True)
    keep = (df["amt_rank"] >= 0.20) & (df["raw_close"] >= 2.0)
    filtered = df.loc[keep, ["score"]]
    n_before = pred.groupby(level="datetime").size().mean()
    n_after = filtered.groupby(level="datetime").size().mean()
    print(f"过滤: 日均 {n_before:.0f} -> {n_after:.0f} 只", flush=True)

    strategy = {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy",
        "kwargs": {"signal": filtered, "topk": 50, "n_drop": 10, "hold_thresh": 1, "only_tradable": True},
    }
    report, _ = backtest_daily(
        start_time="2023-01-01", end_time="2026-07-28", strategy=strategy,
        account=1_000_000, benchmark="SH000300",
        exchange_kwargs={"limit_threshold": 0.095, "deal_price": "close",
                         "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5},
    )
    report.to_pickle("/Users/bytedance/code/qlib/my/artifacts/pool_filtered_report.pkl")
    ex = report["return"] - report["cost"] - report["bench"]
    print("== 全市场+过滤 含成本超额 vs 沪深300 ==", flush=True)
    print(f"  全期={ex.mean()*ANN:+.2%} IR={ex.mean()/ex.std()*ANN**0.5:+.2f}", flush=True)
    for y, g in ex.groupby(ex.index.year):
        print(f"    {y}={g.mean()*ANN:+.2%}", flush=True)
    print(f"  25-08后={ex[ex.index>='2025-08-01'].mean()*ANN:+.2%}  日均换手={report['turnover'].mean():.1%}", flush=True)


if __name__ == "__main__":
    main()

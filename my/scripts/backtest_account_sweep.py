#!/usr/bin/env python3
"""资金规模对照：同一份 pred.pkl，账户 5万 vs 100万，其余参数与现役配置一致。"""

import sys

sys.path.insert(0, "/Users/bytedance/code/qlib")
import pandas as pd
import qlib
from qlib.contrib.evaluate import backtest_daily

ART = "/Users/bytedance/code/qlib/my/mlruns/659009532578438967/408b68e7d8804afd96f66a19522a8229/artifacts"
ANN = 238


def seg_stats(excess, label):
    for name, s in [("全期", excess), ("25-08前", excess[excess.index < "2025-08-01"]), ("25-08后", excess[excess.index >= "2025-08-01"])]:
        ir = s.mean() / s.std() * ANN ** 0.5 if s.std() > 0 else float("nan")
        print(f"  [{label}] {name:7s} 超额年化={s.mean()*ANN:+.2%} IR={ir:+.2f}")


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle(ART + "/pred.pkl")
    for account in (50_000, 1_000_000):
        strategy = {
            "class": "TopkDropoutStrategy",
            "module_path": "qlib.contrib.strategy",
            "kwargs": {"signal": pred, "topk": 50, "n_drop": 2, "hold_thresh": 2},
        }
        report, _ = backtest_daily(
            start_time="2025-01-01",
            end_time="2026-07-28",
            strategy=strategy,
            account=account,
            benchmark="SH000300",
            exchange_kwargs={
                "limit_threshold": 0.095,
                "deal_price": "close",
                "open_cost": 0.0005,
                "close_cost": 0.0015,
                "min_cost": 5,
            },
        )
        excess = report["return"] - report["cost"] - report["bench"]
        print(f"\n===== 账户 {account:,} =====")
        seg_stats(excess, f"{account//10000}万")
        print(f"  日均换手={report['turnover'].mean():.2%}")


if __name__ == "__main__":
    main()

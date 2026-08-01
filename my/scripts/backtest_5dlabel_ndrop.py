#!/usr/bin/env python3
"""5日label 的 pred + 不同换手速度的组合回测（持有期对齐裁决）。"""

import sys

sys.path.insert(0, "/Users/bytedance/code/qlib")
import pandas as pd
import qlib
from qlib.contrib.evaluate import backtest_daily

ART = "/Users/bytedance/code/qlib/my/mlruns/659009532578438967/51caff167b004a2a88faa2bd049cf8f3/artifacts"
ANN = 238
SWEEP = [(5, 1), (10, 1), (15, 1)]


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle(ART + "/pred.pkl")
    for n_drop, hold in SWEEP:
        strategy = {
            "class": "TopkDropoutStrategy",
            "module_path": "qlib.contrib.strategy",
            "kwargs": {"signal": pred, "topk": 50, "n_drop": n_drop, "hold_thresh": hold},
        }
        report, _ = backtest_daily(
            start_time="2025-01-01",
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
        ex = report["return"] - report["cost"] - report["bench"]
        pre, post = ex[ex.index < "2025-08-01"], ex[ex.index >= "2025-08-01"]
        print(
            f"5dlabel n_drop={n_drop:2d} hold={hold} | 全期={ex.mean()*ANN:+.1%} "
            f"25-08前={pre.mean()*ANN:+.1%} 25-08后={post.mean()*ANN:+.1%} "
            f"IR后={post.mean()/post.std()*ANN**0.5:+.2f} 换手={report['turnover'].mean():.1%}"
        )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""实验b：持有期错配裁决。同一份 pred，扫 n_drop（换手速度），账户统一 100 万排除资金干扰。"""

import sys

sys.path.insert(0, "/Users/bytedance/code/qlib")
import pandas as pd
import qlib
from qlib.contrib.evaluate import backtest_daily

ART = "/Users/bytedance/code/qlib/my/mlruns/659009532578438967/408b68e7d8804afd96f66a19522a8229/artifacts"
ANN = 238

# (n_drop, hold_thresh)：hold_thresh=2 会阻碍高换手，n_drop>=10 时放开到 1
SWEEP = [(2, 2), (5, 2), (10, 1), (25, 1)]


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
        excess = report["return"] - report["cost"] - report["bench"]
        pre = excess[excess.index < "2025-08-01"]
        post = excess[excess.index >= "2025-08-01"]
        print(
            f"n_drop={n_drop:2d} hold={hold} | 全期={excess.mean()*ANN:+.1%} "
            f"25-08前={pre.mean()*ANN:+.1%} 25-08后={post.mean()*ANN:+.1%} "
            f"IR后={(post.mean()/post.std())*ANN**0.5:+.2f} 换手={report['turnover'].mean():.1%}"
        )


if __name__ == "__main__":
    main()

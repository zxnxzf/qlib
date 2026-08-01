#!/usr/bin/env python3
"""rolling pred 在 2025-01~2026-07 同窗口回测，与 static 5dlabel n_drop=10 直接对比。"""

import sys

sys.path.insert(0, "/Users/bytedance/code/qlib")
import pandas as pd
import qlib
from qlib.contrib.evaluate import backtest_daily

ANN = 238


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    rolling = pd.read_pickle("/Users/bytedance/code/qlib/my/artifacts/candidate1_pred.pkl")
    strategy = {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy",
        "kwargs": {"signal": rolling, "topk": 50, "n_drop": 10, "hold_thresh": 1},
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
        f"rolling同窗口(25-01~26-07): 25-01~07={pre.mean()*ANN:+.1%}  "
        f"25-08后={post.mean()*ANN:+.1%}  换手={report['turnover'].mean():.1%}"
    )


if __name__ == "__main__":
    main()

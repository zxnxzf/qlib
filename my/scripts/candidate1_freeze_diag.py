#!/usr/bin/env python3
"""冻结诊断：只回测 2023-01~2023-08 拿到冻结时刻的持仓，检查这 50 只票之后的可交易性。"""

import sys

sys.path.insert(0, "/Users/bytedance/code/qlib")
import pandas as pd
import qlib
from qlib.data import D
from qlib.contrib.evaluate import backtest_daily

def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle("/Users/bytedance/code/qlib/my/artifacts/candidate1_pred.pkl")
    strategy = {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy",
        "kwargs": {"signal": pred, "topk": 50, "n_drop": 10, "hold_thresh": 1},
    }
    report, positions = backtest_daily(
        start_time="2023-01-01",
        end_time="2023-08-31",
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
    nz = report[report["turnover"] > 0]
    print("该窗口最后一次交易:", nz.index[-1] if len(nz) else "无")
    last_day = sorted(positions.keys())[-1]
    pos = positions[last_day]
    codes = pos.get_stock_list()
    print(f"冻结持仓 {len(codes)} 只 @ {last_day}")

    # 检查这些票 2023-09 之后的行情覆盖：是否停牌/退市（无数据）
    px = D.features(codes, ["$close", "$volume"], start_time="2023-09-01", end_time="2024-06-30")
    stats = []
    for c in codes:
        try:
            sub = px.loc[c]
            days = sub["$close"].notna().sum()
        except KeyError:
            days = 0
        stats.append((c, days))
    dead = [c for c, d in stats if d < 50]
    print(f"2023-09~2024-06 期间行情覆盖不足50天(疑似停牌/退市)的持仓: {len(dead)} 只")
    print(" ", dead[:20])
    # 冻结日这些持仓的分数排名
    dts = pred.index.get_level_values("datetime")
    day = pred[dts == "2023-07-14"].droplevel(0)["score"]
    rank = day.rank(pct=True)
    held_rank = rank.reindex(codes)
    print(f"冻结日持仓分数分位: 中位={held_rank.median():.2f} 最低={held_rank.min():.2f} 缺分数={held_rank.isna().sum()}只")


if __name__ == "__main__":
    main()

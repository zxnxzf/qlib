#!/usr/bin/env python3
"""实验：主线候选的最低生效资金扫描（capital_sweep）
机制：资金决定每仓预算→可负担票池宽度+佣金地板占比；找净超额贴近100万水平的拐点。
锚点：5万=-2.74%，100万=+13.37%（同口径已测）。"""
import sys
from pathlib import Path
sys.path.insert(0, "/Users/bytedance/code/qlib")
sys.path.insert(0, "/Users/bytedance/code/qlib/my/scripts")
import pandas as pd
import qlib
from qlib.contrib.evaluate import backtest_daily

ART = Path("/Users/bytedance/code/qlib/my/artifacts")
ANN = 238

def run(account, min_cost):
    pred = pd.read_pickle(ART / "candidate1_pred.pkl")
    strategy = {"class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy",
                "kwargs": {"signal": pred, "topk": 50, "n_drop": 2, "hold_thresh": 1, "only_tradable": True}}
    report, _ = backtest_daily(
        start_time="2023-01-01", end_time="2026-07-28", strategy=strategy,
        account=account, benchmark="SH000300",
        exchange_kwargs={"deal_price": "open",
                         "limit_threshold": ("$open/Ref($close,1)-1 > 0.095", "$open/Ref($close,1)-1 < -0.095"),
                         "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": min_cost, "impact_cost": 0.001},
    )
    net = report["return"] - report["cost"] - report["bench"]
    return net.mean() * ANN, report["cost"].mean() * ANN

def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    print("锚点: 5万(min5)=-2.74%  100万(min5)=+13.37%", flush=True)
    for account in (100_000, 200_000, 300_000, 500_000):
        net, cost = run(account, 5)
        print(f"{account//10000}万(min5): 净={net:+.2%} 成本={cost:.1%}", flush=True)
    for account in (100_000, 200_000):
        net, cost = run(account, 0.1)
        print(f"{account//10000}万(免5): 净={net:+.2%} 成本={cost:.1%}", flush=True)

if __name__ == "__main__":
    main()

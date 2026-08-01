#!/usr/bin/env python3
"""v3 成本分解：最低佣金地板(5元/笔)对5万账户的杀伤 + 换手档位。复用 v3 pred。"""
import sys
from pathlib import Path
sys.path.insert(0, "/Users/bytedance/code/qlib")
sys.path.insert(0, "/Users/bytedance/code/qlib/my/scripts")
import pandas as pd
import qlib
from qlib.contrib.evaluate import backtest_daily

ART = Path("/Users/bytedance/code/qlib/my/artifacts")
ANN = 238

def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle(ART / "exp_smallfund_v3_pred.pkl")
    for tag, min_cost, n_drop in (("现状(min5元,n2)", 5, 2), ("免5(n2)", 0.1, 2), ("免5+慢换手(n1)", 0.1, 1)):
        strategy = {"class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy",
                    "kwargs": {"signal": pred, "topk": 25, "n_drop": n_drop, "hold_thresh": 1, "only_tradable": True}}
        report, _ = backtest_daily(
            start_time="2023-01-01", end_time="2026-07-28", strategy=strategy,
            account=50_000, benchmark="SH000300",
            exchange_kwargs={"deal_price": "open",
                             "limit_threshold": ("$open/Ref($close,1)-1 > 0.095", "$open/Ref($close,1)-1 < -0.095"),
                             "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": min_cost, "impact_cost": 0.001},
        )
        gross = report["return"] - report["bench"]
        net = report["return"] - report["cost"] - report["bench"]
        print(f"{tag}: 毛={gross.mean()*ANN:+.2%} 净={net.mean()*ANN:+.2%} 成本={report['cost'].mean()*ANN:.1%} 换手={report['turnover'].mean():.1%} 终值={report['account'].iloc[-1]:,.0f}", flush=True)

if __name__ == "__main__":
    main()

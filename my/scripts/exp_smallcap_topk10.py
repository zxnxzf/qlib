#!/usr/bin/env python3
"""实验：小资金适配版（smallfund_topk10）

假设：5万÷50只=1000元/票买不起一手导致组合变形（决赛科目④ FAIL 的根因）；
浓缩到 topk=10（每票约4750元）后绝大多数股价都能买一手，5万账户净超额转正。
对照：科目④的 5万+topk50（净 -2.7%）；参照：100万+topk50（净 +13.4%）。
口径：开盘执行 + impact 0.001 + n_drop 按比例=1。扫 topk 10/15 两档。
"""

import sys
from pathlib import Path

sys.path.insert(0, "/Users/bytedance/code/qlib")
sys.path.insert(0, "/Users/bytedance/code/qlib/my/scripts")

import pandas as pd
import qlib
from qlib.contrib.evaluate import backtest_daily

ART = Path("/Users/bytedance/code/qlib/my/artifacts")
NAME = "smallfund_topk10"
ANN = 238


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle(ART / "candidate1_pred.pkl")

    results = {}
    for topk in (10, 15):
        strategy = {
            "class": "TopkDropoutStrategy",
            "module_path": "qlib.contrib.strategy",
            "kwargs": {"signal": pred, "topk": topk, "n_drop": 1, "hold_thresh": 1, "only_tradable": True},
        }
        report, _ = backtest_daily(
            start_time="2023-01-01",
            end_time="2026-07-28",
            strategy=strategy,
            account=50_000,
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
        results[topk] = net.mean() * ANN
        yearly = " ".join(f"{y}:{g.mean()*ANN:+.1%}" for y, g in net.groupby(net.index.year))
        print(
            f"topk={topk}: 毛={gross.mean()*ANN:+.2%} 净={net.mean()*ANN:+.2%} IR={ir:+.2f} "
            f"换手={report['turnover'].mean():.1%} 逐年[{yearly}]",
            flush=True,
        )
        if topk == 10:
            report.to_pickle(ART / f"exp_{NAME}_report.pkl")
    print("对照: 5万+topk50 净=-2.74% ；100万+topk50 净=+13.37%", flush=True)

    from exp_mlflow_log import log_experiment

    rid = log_experiment(
        NAME,
        params={"account": 50000, "n_drop": 1, "sweep": "topk 10/15", "base": "open_exec+5dlabel+rolling", "impact": 0.001},
        metrics={f"net_topk{k}": float(v) for k, v in results.items()},
    )
    print(f"mlflow run: {rid}", flush=True)


if __name__ == "__main__":
    main()

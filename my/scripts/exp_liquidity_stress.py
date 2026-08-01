#!/usr/bin/env python3
"""实验：开盘执行流动性压力测试（liquidity_stress_open_exec）

目的：检验榜首 open_exec_rolling 的 +14.6% 毛 / +4.7% 净有多少经得起流动性现实。
① impact_cost 0（对照）/ 0.001 / 0.002 三档重跑；
② 诊断持仓/成交票的流动性暴露：单票 2 万买入额占其当日成交额的比例分布。
"""

import sys
from pathlib import Path

sys.path.insert(0, "/Users/bytedance/code/qlib")
sys.path.insert(0, "/Users/bytedance/code/qlib/my/scripts")

import pandas as pd
import qlib
from qlib.data import D
from qlib.contrib.evaluate import backtest_daily

ART = Path("/Users/bytedance/code/qlib/my/artifacts")
NAME = "liquidity_stress_open_exec"
ANN = 238


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle(ART / "candidate1_pred.pkl")

    results = {}
    for impact in (0.0, 0.001, 0.002):
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
                "deal_price": "open",
                "limit_threshold": ("$open/Ref($close,1)-1 > 0.095", "$open/Ref($close,1)-1 < -0.095"),
                "open_cost": 0.0005,
                "close_cost": 0.0015,
                "min_cost": 5,
                "impact_cost": impact,
            },
        )
        net = report["return"] - report["cost"] - report["bench"]
        gross = report["return"] - report["bench"]
        results[impact] = (gross.mean() * ANN, net.mean() * ANN, net.mean() / net.std() * ANN ** 0.5)
        print(
            f"impact={impact:.3f}: 毛={results[impact][0]:+.2%} 净={results[impact][1]:+.2%} IR={results[impact][2]:+.2f}",
            flush=True,
        )

    # 流动性暴露诊断：每日 TOP50 新入选票，2万/票 占其当日成交额比例
    inst = D.instruments("all_no_bj")
    amt = D.features(inst, ["Ref($amount,-1)"], start_time="2023-01-01", end_time="2026-07-28")
    amt.columns = ["exec_amt"]  # 执行日(T+1)当日成交额（qlib单位）
    df = pred.join(amt, how="inner").dropna(subset=["score"])
    ratios = []
    for dt, g in df.groupby(level="datetime"):
        if len(g) < 500:
            continue
        top = g.nlargest(50, "score")
        # qlib $amount 单位为千元（chenditc/tushare 口径），2万元 = 20 千元
        r = 20.0 / top["exec_amt"].clip(lower=0.001)
        ratios.append(r)
    allr = pd.concat(ratios)
    print("\n== 单票2万买入额 / 当日成交额 的分布（TOP50 候选）==", flush=True)
    for q in (0.5, 0.75, 0.9, 0.99):
        print(f"  P{int(q*100)}: {allr.quantile(q):.3%}", flush=True)
    print(f"  超过 1% 的占比: {(allr > 0.01).mean():.1%}   超过 5% 的占比: {(allr > 0.05).mean():.1%}", flush=True)

    from exp_mlflow_log import log_experiment

    rid = log_experiment(
        NAME,
        params={"base": "open_exec_rolling", "sweep": "impact_cost 0/0.001/0.002"},
        metrics={
            "net_impact0": results[0.0][1],
            "net_impact1": results[0.001][1],
            "net_impact2": results[0.002][1],
            "pct_trades_gt1pct_adv": float((allr > 0.01).mean()),
        },
    )
    print(f"mlflow run: {rid}", flush=True)


if __name__ == "__main__":
    main()

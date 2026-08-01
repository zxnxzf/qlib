#!/usr/bin/env python3
"""实验：小资金方向 v2（smallfund_v2，用户拍板立项）

设计：信号不动（candidate1_pred 滚动5日label），组合约束改造——
  股价上限（一手可负担→5万可养20-30只保分散）+ 流动性下限（防死票爆雷）。
扫两档：A) 价≤20元, topk=25   B) 价≤15元, topk=30。n_drop=2，开盘执行，impact 0.001。
对照：5万 topk10（净-38%死亡螺旋）/ 5万 topk50（净-2.7%变形）/ 100万 topk50 n2（净+13.4%）。
成败判据：净转正且账户曲线无断崖（min账户值）。
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
NAME = "smallfund_v2"
ANN = 238


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle(ART / "candidate1_pred.pkl")
    inst = D.instruments("all_no_bj")
    feats = D.features(inst, ["Mean($amount, 20)", "$close/$factor"], start_time="2023-01-01", end_time="2026-07-28")
    feats.columns = ["amt20", "raw_close"]
    df = pred.join(feats, how="left")
    df["amt_rank"] = df.groupby(level="datetime")["amt20"].rank(pct=True)

    best = None
    for price_cap, topk in ((20.0, 25), (15.0, 30)):
        sig = df.loc[(df["raw_close"] <= price_cap) & (df["raw_close"] >= 2.0) & (df["amt_rank"] >= 0.30), ["score"]]
        n_daily = sig.groupby(level="datetime").size().mean()
        strategy = {
            "class": "TopkDropoutStrategy",
            "module_path": "qlib.contrib.strategy",
            "kwargs": {"signal": sig, "topk": topk, "n_drop": 2, "hold_thresh": 1, "only_tradable": True},
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
        yearly = " ".join(f"{y}:{g.mean()*ANN:+.1%}" for y, g in net.groupby(net.index.year))
        print(
            f"价≤{price_cap:.0f} topk={topk}: 票池日均{n_daily:.0f}只 毛={gross.mean()*ANN:+.2%} "
            f"净={net.mean()*ANN:+.2%} IR={ir:+.2f} 换手={report['turnover'].mean():.1%} "
            f"账户终值={report['account'].iloc[-1]:,.0f} 最低={report['account'].min():,.0f} 逐年[{yearly}]",
            flush=True,
        )
        if best is None or net.mean() > best[2]:
            best = (price_cap, topk, net.mean(), report, sig)

    price_cap, topk, _, report, sig = best
    report.to_pickle(ART / f"exp_{NAME}_report.pkl")
    sig.to_pickle(ART / f"exp_{NAME}_pred.pkl")
    print(f"最优档: 价≤{price_cap:.0f} topk={topk}", flush=True)
    print("对照: 5万topk10净-38.3% / 5万topk50净-2.7% / 100万topk50n2净+13.4%", flush=True)

    import subprocess

    subprocess.run(
        f"/Users/bytedance/code/qlib/.venv/bin/python /Users/bytedance/code/qlib/my/scripts/package_dashboard.py "
        f"{NAME} {ART / f'exp_{NAME}_pred.pkl'} {ART / f'exp_{NAME}_report.pkl'}",
        shell=True,
        check=False,
    )

    from exp_mlflow_log import log_experiment

    net = report["return"] - report["cost"] - report["bench"]
    rid = log_experiment(
        NAME,
        params={"account": 50000, "price_cap": price_cap, "topk": topk, "n_drop": 2,
                "filter": "amt_rank>=0.3 & 2<=price<=cap", "base_signal": "candidate1_pred"},
        metrics={
            "net_excess_ann": net.mean() * ANN,
            "ir_net": net.mean() / net.std() * ANN ** 0.5,
            "account_final": float(report["account"].iloc[-1]),
            "account_min": float(report["account"].min()),
        },
        dashboard=str(ART / "faux_recorders" / NAME / "recorder_dashboard.html"),
    )
    print(f"mlflow run: {rid}", flush=True)


if __name__ == "__main__":
    main()

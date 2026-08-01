#!/usr/bin/env python3
"""实验：过滤入组 × 开盘执行（filtered_open_exec）

假设：流动性/低价过滤（此前毛最高的池子）叠加开盘执行（现榜首），毛超额进一步抬升。
对照组：open_exec_rolling（无过滤，毛 +14.6% / 净 +4.7%）。
唯一变量：入组过滤（20日均成交额截面后20%剔除 + 未复权价<2元剔除）。
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/Users/bytedance/code/qlib")
sys.path.insert(0, "/Users/bytedance/code/qlib/my/scripts")

import pandas as pd
import qlib
from qlib.data import D
from qlib.contrib.evaluate import backtest_daily, risk_analysis

ART = Path("/Users/bytedance/code/qlib/my/artifacts")
NAME = "filtered_open_exec"
ANN = 238


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle(ART / "candidate1_pred.pkl")
    inst = D.instruments("all_no_bj")
    feats = D.features(inst, ["Mean($amount, 20)", "$close/$factor"], start_time="2023-01-01", end_time="2026-07-28")
    feats.columns = ["amt20", "raw_close"]
    df = pred.join(feats, how="left")
    df["amt_rank"] = df.groupby(level="datetime")["amt20"].rank(pct=True)
    filtered = df.loc[(df["amt_rank"] >= 0.20) & (df["raw_close"] >= 2.0), ["score"]]
    print(f"过滤: 日均 {pred.groupby(level='datetime').size().mean():.0f} -> {filtered.groupby(level='datetime').size().mean():.0f} 只", flush=True)

    strategy = {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy",
        "kwargs": {"signal": filtered, "topk": 50, "n_drop": 10, "hold_thresh": 1, "only_tradable": True},
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
        },
    )
    report.to_pickle(ART / f"exp_{NAME}_report.pkl")
    gross = report["return"] - report["bench"]
    net = report["return"] - report["cost"] - report["bench"]
    ir = net.mean() / net.std() * ANN ** 0.5
    print(f"实验组: 毛={gross.mean()*ANN:+.2%} 净={net.mean()*ANN:+.2%} IR={ir:+.2f} 成本={report['cost'].mean()*ANN:.1%} 换手={report['turnover'].mean():.1%}", flush=True)
    for y, g in net.groupby(net.index.year):
        print(f"  净 {y}: {g.mean()*ANN:+.1%}", flush=True)
    print("对照组(无过滤开盘执行): 毛=+14.59% 净=+4.74% IR=+0.25", flush=True)

    root = ART / "faux_recorders" / NAME
    art = root / "artifacts"
    (art / "portfolio_analysis").mkdir(parents=True, exist_ok=True)
    (art / "sig_analysis").mkdir(parents=True, exist_ok=True)
    (root / "metrics").mkdir(parents=True, exist_ok=True)
    filtered.to_pickle(art / "pred.pkl")
    report.to_pickle(art / "portfolio_analysis" / "report_normal_1day.pkl")
    pd.concat(
        {
            "excess_return_without_cost": risk_analysis(gross, freq="day"),
            "excess_return_with_cost": risk_analysis(net, freq="day"),
        }
    ).to_pickle(art / "portfolio_analysis" / "port_analysis_1day.pkl")
    label = D.features(inst, ["Ref($close, -6) / Ref($close, -1) - 1"], start_time="2023-01-01", end_time="2026-07-28")
    label.columns = ["LABEL0"]
    label.reindex(filtered.index).dropna().to_pickle(art / "label.pkl")
    dfl = filtered.join(label, how="inner").dropna()
    ic = dfl.groupby(level="datetime").apply(lambda g: g["score"].corr(g["LABEL0"]))
    ric = dfl.groupby(level="datetime").apply(lambda g: g["score"].corr(g["LABEL0"], method="spearman"))
    ic.to_pickle(art / "sig_analysis" / "ic.pkl")
    ric.to_pickle(art / "sig_analysis" / "ric.pkl")
    for k, v in {
        "IC": ic.mean(),
        "ICIR": ic.mean() / ic.std(),
        "Rank IC": ric.mean(),
        "Rank ICIR": ric.mean() / ric.std(),
        "1day.excess_return_with_cost.annualized_return": net.mean() * ANN,
        "1day.excess_return_with_cost.information_ratio": ir,
    }.items():
        (root / "metrics" / k).write_text(f"0 {v} 0\n")
    subprocess.run(
        f'cd "{root}" && echo "{art}" | /Users/bytedance/code/qlib/.venv/bin/python '
        f"/Users/bytedance/code/qlib/my/scripts/recorder_visualizer_from_path.py > /dev/null 2>&1",
        shell=True,
        check=False,
    )
    print(f"dashboard: {root / 'recorder_dashboard.html'}", flush=True)

    from exp_mlflow_log import log_experiment

    rid = log_experiment(
        NAME,
        params={"base": "open_exec_rolling", "filter": "amt20_rank>=0.2 & raw_close>=2", "control": "open_exec_rolling"},
        metrics={
            "gross_excess_ann": gross.mean() * ANN,
            "net_excess_ann": net.mean() * ANN,
            "ir_net": ir,
            "turnover_daily": report["turnover"].mean(),
            "cost_ann": report["cost"].mean() * ANN,
            "ic": ic.mean(),
            "rank_ic": ric.mean(),
        },
        dashboard=str(root / "recorder_dashboard.html"),
    )
    print(f"mlflow run: {rid}", flush=True)


if __name__ == "__main__":
    main()

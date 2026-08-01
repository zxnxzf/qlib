#!/usr/bin/env python3
"""实验：降频调仓（rebalance_5d，叠加在开盘执行榜首配置上）

假设：信号是 5 日 label，每天调仓是浪费——改为每 5 个交易日调一次，
成本从 ~10%/年 降到 2-4%/年，毛超额损失有限，净超额显著改善。
对照组：open_exec_rolling（每日调仓，毛 +14.6% / 净 +4.7% / 成本 9.8%）。
唯一变量：调仓节奏（每日 → 每 5 日；每次换仓力度 n_drop 相应放大，扫 25/50 两档）。
实现：把信号日历稀释为每 5 个交易日一天，非信号日策略自动不交易。
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
NAME = "rebalance5d_open_exec"
ANN = 238


def run_bt(pred):
    strategy = {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy",
        "kwargs": {"signal": pred, "topk": 50, "n_drop": None, "hold_thresh": 1, "only_tradable": True},
    }
    # n_drop 由外层塞入
    return strategy


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle(ART / "candidate1_pred.pkl")
    dates = sorted(pred.index.get_level_values("datetime").unique())
    keep = set(dates[::5])
    dts = pred.index.get_level_values("datetime")
    pred5 = pred[dts.isin(keep)]
    print(f"信号日: {len(dates)} -> {len(keep)} (每5个交易日一次)", flush=True)

    best = None
    for n_drop in (25, 50):
        strategy = {
            "class": "TopkDropoutStrategy",
            "module_path": "qlib.contrib.strategy",
            "kwargs": {"signal": pred5, "topk": 50, "n_drop": n_drop, "hold_thresh": 1, "only_tradable": True},
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
        gross = report["return"] - report["bench"]
        net = report["return"] - report["cost"] - report["bench"]
        ir = net.mean() / net.std() * ANN ** 0.5
        yearly = {y: g.mean() * ANN for y, g in net.groupby(net.index.year)}
        print(
            f"n_drop={n_drop}: 毛={gross.mean()*ANN:+.2%} 净={net.mean()*ANN:+.2%} IR={ir:+.2f} "
            f"成本={report['cost'].mean()*ANN:.1%} 换手={report['turnover'].mean():.1%} "
            f"逐年净={' '.join(f'{y}:{v:+.1%}' for y, v in yearly.items())}",
            flush=True,
        )
        if best is None or net.mean() > best[1]["net"].mean():
            best = (n_drop, {"report": report, "gross": gross, "net": net})

    n_drop, b = best[0], best[1]
    report, gross, net = b["report"], b["gross"], b["net"]
    report.to_pickle(ART / f"exp_{NAME}_report.pkl")

    # faux recorder + 仪表板
    root = ART / "faux_recorders" / NAME
    art = root / "artifacts"
    (art / "portfolio_analysis").mkdir(parents=True, exist_ok=True)
    (art / "sig_analysis").mkdir(parents=True, exist_ok=True)
    (root / "metrics").mkdir(parents=True, exist_ok=True)
    pred5.to_pickle(art / "pred.pkl")
    report.to_pickle(art / "portfolio_analysis" / "report_normal_1day.pkl")
    pd.concat(
        {
            "excess_return_without_cost": risk_analysis(gross, freq="day"),
            "excess_return_with_cost": risk_analysis(net, freq="day"),
        }
    ).to_pickle(art / "portfolio_analysis" / "port_analysis_1day.pkl")
    inst = D.instruments("all_no_bj")
    label = D.features(inst, ["Ref($close, -6) / Ref($close, -1) - 1"], start_time="2023-01-01", end_time="2026-07-28")
    label.columns = ["LABEL0"]
    label.reindex(pred5.index).dropna().to_pickle(art / "label.pkl")
    dfl = pred5.join(label, how="inner").dropna()
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
        "1day.excess_return_with_cost.information_ratio": net.mean() / net.std() * ANN ** 0.5,
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
        params={
            "base": "open_exec_rolling",
            "rebalance": "every 5 trade days",
            "n_drop_per_rebalance": n_drop,
            "control": "open_exec_rolling(daily)",
        },
        metrics={
            "gross_excess_ann": gross.mean() * ANN,
            "net_excess_ann": net.mean() * ANN,
            "ir_net": net.mean() / net.std() * ANN ** 0.5,
            "turnover_daily": report["turnover"].mean(),
            "cost_ann": report["cost"].mean() * ANN,
        },
        dashboard=str(root / "recorder_dashboard.html"),
    )
    print(f"mlflow run: {rid} (best n_drop={n_drop})", flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""实验：T+1 开盘价执行（open_exec_rolling）

假设：改为 T+1 开盘成交后，盘中才封板的票可以买入（仅一字板不可），
涨停拦截率大降、可成交头部超额转正、毛超额提升。
对照组：candidate1_only_tradable（同 pred、收盘成交、收盘涨停判定），
        全期毛 +5.8% / 净 -3.9%（my/artifacts/candidate1_report_tradable.pkl）。
唯一变量：执行时点（成交价 close→open，涨跌停判定同步换到开盘口径）。
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
NAME = "open_exec_rolling"
ANN = 238


def seg(ex, label):
    ir = ex.mean() / ex.std() * ANN ** 0.5 if ex.std() > 0 else float("nan")
    print(f"  {label:26s} 年化={ex.mean()*ANN:+.2%} IR={ir:+.2f}", flush=True)


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle(ART / "candidate1_pred.pkl")

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
        },
    )
    report.to_pickle(ART / f"exp_{NAME}_report.pkl")

    gross = report["return"] - report["bench"]
    net = report["return"] - report["cost"] - report["bench"]
    print("== 实验组（开盘执行）==", flush=True)
    seg(gross, "毛超额 全期")
    seg(net, "净超额 全期")
    for y, g in net.groupby(net.index.year):
        seg(g, f"  净 {y}")
    print(f"  日均换手={report['turnover'].mean():.1%}  成本={report['cost'].mean()*ANN:.1%}/年", flush=True)

    ctrl = pd.read_pickle(ART / "candidate1_report_tradable.pkl")
    cg = ctrl["return"] - ctrl["bench"]
    cn = ctrl["return"] - ctrl["cost"] - ctrl["bench"]
    print("== 对照组（收盘执行）==", flush=True)
    seg(cg, "毛超额 全期")
    seg(cn, "净超额 全期")

    # 拦截率对照：每日 TOP50 中"开盘即一字"的比例 vs 原"收盘涨停"的比例
    inst = D.instruments("all_no_bj")
    feats = D.features(
        inst,
        ["Ref($open,-1)/$close-1", "Ref($close,-1)/$close-1", "Ref($volume,-1)"],
        start_time="2023-01-01",
        end_time="2026-07-28",
    )
    feats.columns = ["open_chg", "close_chg", "exec_vol"]
    df = pred.join(feats, how="inner").dropna(subset=["score"])
    blocked_open, blocked_close, n_days = 0.0, 0.0, 0
    for dt, g in df.groupby(level="datetime"):
        if len(g) < 500:
            continue
        top = g.nlargest(50, "score")
        blocked_open += ((top["open_chg"] > 0.095) | (top["exec_vol"].fillna(0) <= 0)).sum()
        blocked_close += ((top["close_chg"] > 0.095) | (top["exec_vol"].fillna(0) <= 0)).sum()
        n_days += 1
    print(f"== 拦截率：TOP50 日均被拦 开盘口径={blocked_open/n_days:.1f}/50  收盘口径={blocked_close/n_days:.1f}/50", flush=True)

    # faux recorder 打包（复用 build_faux_recorders 的结构约定）
    root = ART / "faux_recorders" / NAME
    art = root / "artifacts"
    (art / "portfolio_analysis").mkdir(parents=True, exist_ok=True)
    (art / "sig_analysis").mkdir(parents=True, exist_ok=True)
    (root / "metrics").mkdir(parents=True, exist_ok=True)
    pred.to_pickle(art / "pred.pkl")
    report.to_pickle(art / "portfolio_analysis" / "report_normal_1day.pkl")
    pd.concat(
        {
            "excess_return_without_cost": risk_analysis(gross, freq="day"),
            "excess_return_with_cost": risk_analysis(net, freq="day"),
        }
    ).to_pickle(art / "portfolio_analysis" / "port_analysis_1day.pkl")
    label = D.features(inst, ["Ref($close, -6) / Ref($close, -1) - 1"], start_time="2023-01-01", end_time="2026-07-28")
    label.columns = ["LABEL0"]
    label.reindex(pred.index).dropna().to_pickle(art / "label.pkl")
    dfl = pred.join(label, how="inner").dropna()
    ic = dfl.groupby(level="datetime").apply(lambda g: g["score"].corr(g["LABEL0"]))
    ric = dfl.groupby(level="datetime").apply(lambda g: g["score"].corr(g["LABEL0"], method="spearman"))
    ic.to_pickle(art / "sig_analysis" / "ic.pkl")
    ric.to_pickle(art / "sig_analysis" / "ric.pkl")
    metrics = {
        "IC": ic.mean(),
        "ICIR": ic.mean() / ic.std(),
        "Rank IC": ric.mean(),
        "Rank ICIR": ric.mean() / ric.std(),
        "1day.excess_return_with_cost.annualized_return": net.mean() * ANN,
        "1day.excess_return_with_cost.information_ratio": net.mean() / net.std() * ANN ** 0.5,
    }
    for k, v in metrics.items():
        (root / "metrics" / k).write_text(f"0 {v} 0\n")

    # 仪表板
    subprocess.run(
        f'cd "{root}" && echo "{art}" | /Users/bytedance/code/qlib/.venv/bin/python '
        f"/Users/bytedance/code/qlib/my/scripts/recorder_visualizer_from_path.py > /dev/null 2>&1",
        shell=True,
        check=False,
    )
    print(f"dashboard: {root / 'recorder_dashboard.html'}", flush=True)

    # mlflow 落档
    from exp_mlflow_log import log_experiment

    rid = log_experiment(
        NAME,
        params={
            "pool": "all_no_bj",
            "label": "5d",
            "rolling": "quarterly",
            "deal_price": "open",
            "limit": "open-based 0.095",
            "control": "candidate1_only_tradable(close-exec)",
        },
        metrics={
            "gross_excess_ann": gross.mean() * ANN,
            "net_excess_ann": net.mean() * ANN,
            "ir_net": net.mean() / net.std() * ANN ** 0.5,
            "turnover_daily": report["turnover"].mean(),
            "cost_ann": report["cost"].mean() * ANN,
            "blocked_top50_open": blocked_open / n_days,
            "blocked_top50_close": blocked_close / n_days,
        },
        dashboard=str(root / "recorder_dashboard.html"),
    )
    print(f"mlflow run: {rid}", flush=True)


if __name__ == "__main__":
    main()

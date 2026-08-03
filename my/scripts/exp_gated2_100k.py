#!/usr/bin/env python3
"""实验#11引擎级：门控V2（MA20+3天确认离场 + 全指单日涨>2.5%强制次日回场），10万口径。
对照：门控V1引擎版（年化+18.1% 回撤-24.1% 净+11.5% IR0.59）。沙盘邻域2.0-3.5%全稳健。"""
import subprocess, sys
from pathlib import Path
sys.path.insert(0, "/Users/bytedance/code/qlib")
sys.path.insert(0, "/Users/bytedance/code/qlib/my/scripts")
import pandas as pd
import qlib
from qlib.data import D
from qlib.contrib.evaluate import backtest_daily
from exp_gated_100k import GatedTopkDropout

ART = Path("/Users/bytedance/code/qlib/my/artifacts")
NAME = "gated2_100k"
ANN = 238

def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle(ART / "candidate1_pred.pkl")
    idx = D.features(["SH000985"], ["$close"], start_time="2022-10-01", end_time="2026-07-28").droplevel(0)["$close"]
    above = idx > idx.rolling(20).mean()
    surge = idx.pct_change() > 0.025
    off = ((~above).rolling(3).sum() >= 3) & (~surge)
    gate = (~off).shift(1); gate.index = pd.to_datetime(gate.index); gate = gate.fillna(True)
    strategy = GatedTopkDropout(gate=gate, signal=pred, topk=50, n_drop=2, hold_thresh=1, only_tradable=True)
    report, _ = backtest_daily(
        start_time="2023-01-01", end_time="2026-07-28", strategy=strategy,
        account=100_000, benchmark="SH000300",
        exchange_kwargs={"deal_price": "open",
                         "limit_threshold": ("$open/Ref($close,1)-1 > 0.095", "$open/Ref($close,1)-1 < -0.095"),
                         "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5, "impact_cost": 0.001},
    )
    report.to_pickle(ART / f"exp_{NAME}_report.pkl")
    net = report["return"] - report["cost"]
    cum = (1+net).cumprod(); mdd = (cum/cum.cummax()-1).min()
    ex = net - report["bench"]; excum = ex.cumsum(); exgive = (excum - excum.cummax()).min()
    print(f"门控V2(10万): 年化={cum.iloc[-1]**(ANN/len(net))-1:+.1%} 回撤={mdd:.1%} 净超额={ex.mean()*ANN:+.2%} IR={ex.mean()/ex.std()*ANN**0.5:+.2f} 超额回吐={exgive:.1%} 终值={report['account'].iloc[-1]:,.0f}", flush=True)
    subprocess.run(f"/Users/bytedance/code/qlib/.venv/bin/python /Users/bytedance/code/qlib/my/scripts/package_dashboard.py {NAME} {ART/'candidate1_pred.pkl'} {ART/f'exp_{NAME}_report.pkl'}", shell=True, check=False)
    from exp_mlflow_log import log_experiment
    rid = log_experiment(NAME, params={"gate": "MA20+3day + surge2.5% fast-reentry", "account": 100000},
        metrics={"net_excess_ann": ex.mean()*ANN, "abs_mdd": float(mdd), "ir_net": ex.mean()/ex.std()*ANN**0.5})
    print(f"mlflow run: {rid}", flush=True)

if __name__ == "__main__":
    main()

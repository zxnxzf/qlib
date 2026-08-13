#!/usr/bin/env python3
"""门控温度计换 SH000905 的两道刑讯：①参数邻域稳健性沙盘 ②引擎级复验（10万口径）。"""

import sys

sys.path.insert(0, "/Users/bytedance/code/qlib")
sys.path.insert(0, "/Users/bytedance/code/qlib/my")

import pandas as pd

from quant import data
data.init_qlib(kernels=4)
from qlib.data import D
from qlib.contrib.evaluate import backtest_daily

sys.path.insert(0, "/Users/bytedance/code/qlib/my/scripts")
from exp_gated_100k import GatedTopkDropout

ANN = 238
ART = "/Users/bytedance/code/qlib/my/artifacts"


def build_gate(closes, ma, confirm, surge_th):
    above = closes > closes.rolling(ma).mean()
    surge = closes.pct_change() > surge_th
    off = ((~above).rolling(confirm).sum() >= confirm) & (~surge)
    return ~off


def main():
    closes = D.features(["SH000905"], ["$close"], start_time="2020-06-01", end_time="2026-08-03").droplevel(0)["$close"]
    closes.index = pd.to_datetime(closes.index)

    r = pd.read_pickle(ART + "/exp_longwindow_ndrop2_open_exec_report.pkl")
    net = r["return"] - r["cost"]; bench = r["bench"]

    print("== ①邻域稳健性（905温度计，长窗沙盘）==", flush=True)
    for ma, confirm, st in ((20, 3, 0.025), (15, 3, 0.025), (30, 3, 0.025), (20, 2, 0.025), (20, 4, 0.025), (20, 3, 0.02), (20, 3, 0.03)):
        g = build_gate(closes, ma, confirm, st)
        w = g.astype(float).shift(1).reindex(net.index).ffill().fillna(1.0)
        s = net * w - w.diff().abs().fillna(0) * 0.002
        cum = (1 + s).cumprod(); mdd = (cum / cum.cummax() - 1).min()
        ex = s - bench
        print(f"  MA{ma}/确认{confirm}天/回场{st:.1%}: 年化={cum.iloc[-1]**(ANN/len(s))-1:+.1%} 回撤={mdd:.1%} IR={ex.mean()/ex.std()*ANN**0.5:+.2f}", flush=True)

    print("== ②引擎级复验（10万，短窗2023-2026）==", flush=True)
    pred = pd.read_pickle(ART + "/candidate1_pred.pkl")
    g = build_gate(closes, 20, 3, 0.025)
    gate = g.shift(1); gate.index = pd.to_datetime(gate.index); gate = gate.fillna(True)
    strategy = GatedTopkDropout(gate=gate, signal=pred, topk=50, n_drop=2, hold_thresh=1, only_tradable=True)
    report, _ = backtest_daily(
        start_time="2023-01-01", end_time="2026-07-28", strategy=strategy,
        account=100_000, benchmark="SH000300",
        exchange_kwargs={"deal_price": "open",
                         "limit_threshold": ("$open/Ref($close,1)-1 > 0.095", "$open/Ref($close,1)-1 < -0.095"),
                         "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5, "impact_cost": 0.001},
    )
    report.to_pickle(ART + "/exp_gate905_100k_report.pkl")
    n = report["return"] - report["cost"]
    cum = (1 + n).cumprod(); mdd = (cum / cum.cummax() - 1).min()
    ex = n - report["bench"]
    print(f"  905门控引擎版: 年化={cum.iloc[-1]**(ANN/len(n))-1:+.1%} 回撤={mdd:.1%} 净超额={ex.mean()*ANN:+.2%} IR={ex.mean()/ex.std()*ANN**0.5:+.2f}", flush=True)
    print("  对照 985门控引擎版(残): 年化=+18.2% 回撤=-24.1% 净超额=+11.77% IR=+0.64", flush=True)


if __name__ == "__main__":
    main()

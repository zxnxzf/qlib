#!/usr/bin/env python3
"""门控温度计替换沙盘：SH000985 已在上游数据包中断更（2026-07-06 起），
用长窗对照检验替代品：中证500 / 中证1000 / 全池等权自建指数。规则不变仅换温度计。"""

import sys

sys.path.insert(0, "/Users/bytedance/code/qlib")
sys.path.insert(0, "/Users/bytedance/code/qlib/my")

import pandas as pd

from quant import config as C
from quant import data, gate


def main():
    data.init_qlib(kernels=4)
    from qlib.data import D

    r = pd.read_pickle("/Users/bytedance/code/qlib/my/artifacts/exp_longwindow_ndrop2_open_exec_report.pkl")
    net = r["return"] - r["cost"]
    bench = r["bench"]
    ANN = 238

    def series_for(code):
        s = D.features([code], ["$close"], start_time="2020-06-01", end_time="2026-08-03").droplevel(0)["$close"]
        s.index = pd.to_datetime(s.index)
        return s

    inst = D.instruments(C.POOL)
    chg = D.features(inst, ["$change"], start_time="2020-06-01", end_time="2026-08-03")
    eq = chg.groupby(level="datetime")["$change"].mean()
    eq.index = pd.to_datetime(eq.index)
    eq_idx = (1 + eq.fillna(0)).cumprod()
    eq_idx.to_pickle("/Users/bytedance/code/qlib/my/artifacts/eq_pool_index.pkl")

    def prof(closes, name):
        g = gate.gate_series(closes)
        w = g.astype(float).shift(1).reindex(net.index).ffill().fillna(1.0)
        s = net * w - w.diff().abs().fillna(0) * 0.002
        cum = (1 + s).cumprod()
        mdd = (cum / cum.cummax() - 1).min()
        ex = s - bench
        print(f"  {name:20s} 年化={cum.iloc[-1]**(ANN/len(s))-1:+.1%} 回撤={mdd:.1%} "
              f"IR={ex.mean()/ex.std()*ANN**0.5:+.2f} 在场率={w.mean():.0%}", flush=True)

    print("== 门控温度计替换沙盘（长窗，规则不变仅换温度计）==", flush=True)
    prof(series_for("SH000985"), "旧:中证全指(残)")
    prof(series_for("SH000905"), "候选A:中证500")
    prof(series_for("SH000852"), "候选A2:中证1000")
    prof(eq_idx, "候选B:全池等权自建")


if __name__ == "__main__":
    main()

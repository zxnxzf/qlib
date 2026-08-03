#!/usr/bin/env python3
"""仓位系数引擎级回测：risk_degree 实现仓位打折，暴露仓位×资金门槛的相互作用。
对照：gated2_100k（10万满仓=risk_degree0.95，净+11.8% 回撤-24.1%）。
算术近似预测：40%仓位→回撤-10.1%/年化+8.2%——引擎见真章。"""
import sys
from pathlib import Path
sys.path.insert(0, "/Users/bytedance/code/qlib")
sys.path.insert(0, "/Users/bytedance/code/qlib/my/scripts")
import pandas as pd
import qlib
from qlib.data import D
from qlib.contrib.evaluate import backtest_daily
from exp_gated_100k import GatedTopkDropout

ART = Path("/Users/bytedance/code/qlib/my/artifacts")
ANN = 238

def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle(ART / "candidate1_pred.pkl")
    idx = D.features(["SH000985"], ["$close"], start_time="2022-10-01", end_time="2026-07-28").droplevel(0)["$close"]
    above = idx > idx.rolling(20).mean()
    surge = idx.pct_change() > 0.025
    off = ((~above).rolling(3).sum() >= 3) & (~surge)
    gate = (~off).shift(1); gate.index = pd.to_datetime(gate.index); gate = gate.fillna(True)

    cases = [("子账户4万(=10万×40%)", 40_000, 0.95), ("子账户7万(=10万×70%)", 70_000, 0.95), ("子账户8万(=10万×80%)", 80_000, 0.95)]
    for tag, account, rd in cases:
        strategy = GatedTopkDropout(gate=gate, signal=pred, topk=50, n_drop=2, hold_thresh=1,
                                    only_tradable=True, risk_degree=rd)
        report, _ = backtest_daily(
            start_time="2023-01-01", end_time="2026-07-28", strategy=strategy,
            account=account, benchmark="SH000300",
            exchange_kwargs={"deal_price": "open",
                             "limit_threshold": ("$open/Ref($close,1)-1 > 0.095", "$open/Ref($close,1)-1 < -0.095"),
                             "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5, "impact_cost": 0.001},
        )
        net = report["return"] - report["cost"]
        cum = (1+net).cumprod(); mdd = (cum/cum.cummax()-1).min()
        print(f"{tag}: 年化={cum.iloc[-1]**(ANN/len(net))-1:+.1%} 回撤={mdd:.1%} 终值={report['account'].iloc[-1]:,.0f}", flush=True)
    print("对照 10万×满仓(已测): 年化=+18.2% 回撤=-24.1%", flush=True)
    print("算术近似曾预测 40%仓: 年化=+8.2% 回撤=-10.1%", flush=True)

if __name__ == "__main__":
    main()

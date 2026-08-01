#!/usr/bin/env python3
"""实验a：涨停逆向选择裁决。

TOP50 的纸面收益 vs 剔除"执行日（T+1）收盘涨停/停牌买不进"后的可成交收益。
口径与回测一致：limit_threshold=0.095（执行日涨幅 >=9.5% 视为买不进），成交量缺失/为0 视为停牌。
label = Ref($close,-2)/Ref($close,-1)-1（T+1 收盘买入 -> T+2 收盘的收益）。
"""

import sys

sys.path.insert(0, "/Users/bytedance/code/qlib")
import pandas as pd
import qlib
from qlib.data import D

ART = "/Users/bytedance/code/qlib/my/mlruns/659009532578438967/408b68e7d8804afd96f66a19522a8229/artifacts"
ANN = 238


def period_stats(df, title):
    rows = {}
    for dt, g in df.groupby(level="datetime"):
        if len(g) < 500:
            continue
        top = g.nlargest(50, "score")
        buyable = top[top["tradable"]]
        blocked = top[~top["tradable"]]
        rows[dt] = {
            "paper": top["label"].mean(),
            "buyable": buyable["label"].mean() if len(buyable) else float("nan"),
            "blocked": blocked["label"].mean() if len(blocked) else float("nan"),
            "n_blocked": len(blocked),
            "mkt": g["label"].mean(),
        }
    r = pd.DataFrame(rows).T
    print(f"\n== {title} ==")
    print(f"  纸面TOP50年化:       {r['paper'].mean()*ANN:+.1%}")
    print(f"  可成交TOP50年化:     {r['buyable'].mean()*ANN:+.1%}")
    print(f"  被拦截票的年化:      {r['blocked'].mean()*ANN:+.1%}  (日均被拦 {r['n_blocked'].mean():.1f}/50 只)")
    print(f"  全市场年化:          {r['mkt'].mean()*ANN:+.1%}")
    print(f"  头部超额: 纸面={((r['paper']-r['mkt']).mean()*ANN):+.1%}  可成交={((r['buyable']-r['mkt']).mean()*ANN):+.1%}")


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle(ART + "/pred.pkl")
    inst = D.instruments("all_no_bj")
    feats = D.features(
        inst,
        [
            "Ref($close,-2)/Ref($close,-1)-1",  # label
            "Ref($close,-1)/$close-1",          # 执行日(T+1)涨幅
            "Ref($volume,-1)",                  # 执行日成交量
        ],
        start_time="2025-01-01",
        end_time="2026-07-28",
    )
    feats.columns = ["label", "exec_chg", "exec_vol"]
    df = pred.join(feats, how="inner")
    df = df.dropna(subset=["label", "score"])
    df["tradable"] = (df["exec_chg"] < 0.095) & (df["exec_vol"].fillna(0) > 0)
    dts = df.index.get_level_values("datetime")
    period_stats(df[dts < "2025-08-01"], "2025-08 前")
    period_stats(df[dts >= "2025-08-01"], "2025-08 后（纯样本外）")


if __name__ == "__main__":
    main()

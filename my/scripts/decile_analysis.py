#!/usr/bin/env python3
"""预测分数分层验证：每天按 score 分 10 档，看各档次日真实收益（Alpha158 同款 label）。"""

import sys

sys.path.insert(0, "/Users/bytedance/code/qlib")
import pandas as pd
import qlib
from qlib.data import D

ART = "/Users/bytedance/code/qlib/my/mlruns/659009532578438967/408b68e7d8804afd96f66a19522a8229/artifacts"
ANN = 238


def decile_stats(d, title):
    rows = {}
    for dt, g in d.groupby(level="datetime"):
        if len(g) < 500:
            continue
        q = pd.qcut(g["score"].rank(method="first"), 10, labels=False)
        m = g.groupby(q)["label"].mean()
        rows[dt] = {
            **{f"D{int(k)+1}": v for k, v in m.items()},
            "TOP50": g.nlargest(50, "score")["label"].mean(),
            "MKT": g["label"].mean(),
        }
    r = pd.DataFrame(rows).T
    print(f"\n== 分层日均收益年化 {title} (D10=分数最高档) ==")
    print("  " + "  ".join(f"{c}={r[c].mean()*ANN:+.1%}" for c in ["D1", "D3", "D5", "D8", "D10", "TOP50", "MKT"]))
    print(f"  头部超额: TOP50-MKT = {(r['TOP50'] - r['MKT']).mean()*ANN:+.1%}   多空: D10-D1 = {(r['D10'] - r['D1']).mean()*ANN:+.1%}")


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle(ART + "/pred.pkl")
    inst = D.instruments("all_no_bj")
    label = D.features(inst, ["Ref($close,-2)/Ref($close,-1)-1"], start_time="2025-01-01", end_time="2026-07-28")
    label.columns = ["label"]
    df = pred.join(label, how="inner").dropna()
    dts = df.index.get_level_values("datetime")
    decile_stats(df[dts < "2025-08-01"], "2025-08 前")
    decile_stats(df[dts >= "2025-08-01"], "2025-08 后（纯样本外）")


if __name__ == "__main__":
    main()

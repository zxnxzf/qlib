#!/usr/bin/env python3
"""5日 label 实验 vs 现役复刻：分段超额 + 可成交性分层对比。"""

import sys

sys.path.insert(0, "/Users/bytedance/code/qlib")
import pandas as pd
import qlib
from qlib.data import D

BASE = "/Users/bytedance/code/qlib/my/mlruns/659009532578438967"
RECS = {
    "1日label(现役)": f"{BASE}/408b68e7d8804afd96f66a19522a8229/artifacts",
    "5日label": f"{BASE}/51caff167b004a2a88faa2bd049cf8f3/artifacts",
}
ANN = 238


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)

    print("== 组合层：含成本超额（vs 沪深300）==")
    for name, art in RECS.items():
        r = pd.read_pickle(art + "/portfolio_analysis/report_normal_1day.pkl")
        ex = r["return"] - r["cost"] - r["bench"]
        pre, post = ex[ex.index < "2025-08-01"], ex[ex.index >= "2025-08-01"]
        print(
            f"  {name:12s} 全期={ex.mean()*ANN:+.1%}  25-08前={pre.mean()*ANN:+.1%}  "
            f"25-08后={post.mean()*ANN:+.1%} (IR={post.mean()/post.std()*ANN**0.5:+.2f})  换手={r['turnover'].mean():.1%}"
        )

    # 可成交性分层（口径同 top50_tradability_analysis.py，label 统一用1日收益衡量真实赚钱能力）
    inst = D.instruments("all_no_bj")
    feats = D.features(
        inst,
        ["Ref($close,-2)/Ref($close,-1)-1", "Ref($close,-1)/$close-1", "Ref($volume,-1)"],
        start_time="2025-01-01",
        end_time="2026-07-28",
    )
    feats.columns = ["label", "exec_chg", "exec_vol"]
    print("\n== 信号层：TOP50 次日收益（纯样本外 25-08 后）==")
    for name, art in RECS.items():
        pred = pd.read_pickle(art + "/pred.pkl")
        df = pred.join(feats, how="inner").dropna(subset=["label", "score"])
        df["tradable"] = (df["exec_chg"] < 0.095) & (df["exec_vol"].fillna(0) > 0)
        dts = df.index.get_level_values("datetime")
        d = df[dts >= "2025-08-01"]
        rows = {}
        for dt, g in d.groupby(level="datetime"):
            if len(g) < 500:
                continue
            top = g.nlargest(50, "score")
            buyable = top[top["tradable"]]
            rows[dt] = {
                "paper": top["label"].mean(),
                "buyable": buyable["label"].mean() if len(buyable) else float("nan"),
                "n_blocked": 50 - len(buyable),
                "mkt": g["label"].mean(),
            }
        r = pd.DataFrame(rows).T
        print(
            f"  {name:12s} 纸面超额={(r['paper']-r['mkt']).mean()*ANN:+.1%}  "
            f"可成交超额={(r['buyable']-r['mkt']).mean()*ANN:+.1%}  日均被拦={r['n_blocked'].mean():.1f}/50"
        )


if __name__ == "__main__":
    main()

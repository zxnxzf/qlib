#!/usr/bin/env python3
"""现役策略复刻实验（2025-01~2026-07 回测）的验收单分析。"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "/Users/bytedance/code/qlib")
import qlib  # noqa: E402
from qlib.data import D  # noqa: E402

REC = Path("/Users/bytedance/code/qlib/my/mlruns/659009532578438967/408b68e7d8804afd96f66a19522a8229")
ART = REC / "artifacts"

qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn")

# --- 信号质量指标（SigAnaRecord 已写入 mlflow metrics）---
metrics = {}
mdir = REC / "metrics"
for f in mdir.iterdir():
    val = f.read_text().strip().split("\n")[-1].split(" ")[1]
    metrics[f.name] = float(val)
print("== 信号质量 ==")
for k in ("IC", "ICIR", "Rank IC", "Rank ICIR"):
    if k in metrics:
        print(f"  {k}: {metrics[k]:.4f}")

# --- 组合层：日度明细 ---
report = pd.read_pickle(ART / "portfolio_analysis" / "report_normal_1day.pkl")
excess = (report["return"] - report["cost"] - report["bench"]).rename("excess")  # 含成本超额
ann = 238


def stats(s: pd.Series, label: str):
    n = len(s)
    ar = s.mean() * ann
    ir = (s.mean() / s.std()) * (ann ** 0.5) if s.std() > 0 else float("nan")
    cum = (1 + s).cumprod()
    mdd = (cum / cum.cummax() - 1).min()
    print(f"  {label:26s} 天数={n:4d} 超额年化={ar:+.2%} IR={ir:+.2f} 超额最大回撤={mdd:.2%}")


print("\n== 含成本超额（vs 沪深300）==")
stats(excess, "全区间 2025-01 ~ 2026-07")
for year, grp in excess.groupby(excess.index.year):
    stats(grp, f"  {year} 年")
stats(excess[excess.index < "2025-08-01"], "  2025-01~07（你当年看过）")
stats(excess[excess.index >= "2025-08-01"], "  2025-08~26-07（纯样本外）")

print("\n== 绝对收益对照 ==")
strat = report["return"] - report["cost"]
print(f"  策略累计(含成本): {(1+strat).prod()-1:+.2%}   沪深300累计: {(1+report['bench']).prod()-1:+.2%}")
print(f"  日均换手率: {report['turnover'].mean():.2%}")

# --- vs 中证全指诊断 ---
idx = D.features(["SH000985"], ["$close"], start_time="2024-12-20", end_time="2026-07-28")
allret = idx.droplevel(0)["$close"].pct_change().reindex(report.index).fillna(0.0)
excess_all = (strat - allret).rename("excess_vs_csiall")
print("\n== 诊断：vs 中证全指 SH000985 ==")
stats(excess_all, "全区间")
stats(excess_all[excess_all.index >= "2025-08-01"], "  纯样本外段")

# --- quantstats 体检报告 ---
try:
    import quantstats as qs
    out = Path(__file__).parent / "quantstats_report_all_no_bj_2025_2026.html"
    qs.reports.html(strat, benchmark=report["bench"].rename("CSI300"),
                    output=str(out), title="现役策略复刻 2025-01~2026-07 (all_no_bj)")
    print(f"\nquantstats 报告: {out}")
except Exception as err:
    print("quantstats 失败:", err)

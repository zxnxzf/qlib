#!/usr/bin/env python3
"""qlib 回测结果 -> quantstats HTML 报告的胶水验证。

qlib 的 PortAnaRecord 产物 report_normal_1day.pkl 每日一行：
  return（组合日收益，费前）、cost（当日交易成本占比）、bench（基准日收益）等。
quantstats 只需要日收益 Series，因此：
  策略收益（含成本） = return - cost
  基准收益           = bench
"""

import sys
from pathlib import Path

import pandas as pd
import quantstats as qs

ART = Path("/Users/bytedance/code/qlib/my/mlruns/583100969444960600/32fb882581004afa9ac2e9c5e95123db/artifacts")
OUT = Path(__file__).parent / "quantstats_report_lgb_2017_2020.html"

report = pd.read_pickle(ART / "portfolio_analysis" / "report_normal_1day.pkl")
print("qlib report 列:", list(report.columns))

returns = (report["return"] - report["cost"]).rename("LGB_Alpha158")  # 含成本日收益
bench = report["bench"].rename("CSI300")

qs.reports.html(returns, benchmark=bench, output=str(OUT), title="LGB Alpha158 vs CSI300 (2017-2020)")
print("HTML 报告:", OUT)

# 顺手打印几个核心指标，和 qlib 自己的 risk_analysis 对一下口径
print("年化(含成本):", f"{qs.stats.cagr(returns):.4f}")
print("夏普:", f"{qs.stats.sharpe(returns):.3f}")
print("最大回撤:", f"{qs.stats.max_drawdown(returns):.4f}")
excess = returns - bench
print("超额年化(算术,×238):", f"{excess.mean()*238:.4f}")

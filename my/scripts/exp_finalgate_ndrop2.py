#!/usr/bin/env python3
"""决赛科目考试：候选 = 开盘执行 + n_drop2 + 5日label + 滚动 + all_no_bj，口径含 impact 0.001。

科目：①逐年分解（至多1负年）②参数敏感性（n_drop 1/3，topk 40/60，净超额不变号）
     ③成本翻倍（净保留>60%）④5万真实资金复测。
基线成绩（frontier 已跑）：毛+17.79% 净+13.37% IR+0.72。
"""

import sys
from pathlib import Path

sys.path.insert(0, "/Users/bytedance/code/qlib")
sys.path.insert(0, "/Users/bytedance/code/qlib/my/scripts")

import pandas as pd
import qlib
from qlib.contrib.evaluate import backtest_daily

ART = Path("/Users/bytedance/code/qlib/my/artifacts")
ANN = 238
BASE_NET = 0.1337


def run(topk, n_drop, account, cost_mult=1.0):
    pred = pd.read_pickle(ART / "candidate1_pred.pkl")
    strategy = {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy",
        "kwargs": {"signal": pred, "topk": topk, "n_drop": n_drop, "hold_thresh": 1, "only_tradable": True},
    }
    report, _ = backtest_daily(
        start_time="2023-01-01",
        end_time="2026-07-28",
        strategy=strategy,
        account=account,
        benchmark="SH000300",
        exchange_kwargs={
            "deal_price": "open",
            "limit_threshold": ("$open/Ref($close,1)-1 > 0.095", "$open/Ref($close,1)-1 < -0.095"),
            "open_cost": 0.0005 * cost_mult,
            "close_cost": 0.0015 * cost_mult,
            "min_cost": 5,
            "impact_cost": 0.001 * cost_mult,
        },
    )
    net = report["return"] - report["cost"] - report["bench"]
    return report, net


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)

    # 科目① 逐年（用 frontier 保存的基线 report）
    base = pd.read_pickle(ART / "exp_ndrop_frontier_open_exec_best_ndrop2_report.pkl")
    bnet = base["return"] - base["cost"] - base["bench"]
    print("== 科目① 逐年净超额（至多1负年）==", flush=True)
    neg = 0
    for y, g in bnet.groupby(bnet.index.year):
        v = g.mean() * ANN
        neg += v < 0
        print(f"  {y}: {v:+.1%}", flush=True)
    print(f"  负年数: {neg}  {'PASS' if neg <= 1 else 'FAIL'}", flush=True)

    # 科目② 敏感性
    print("== 科目② 参数敏感性（净超额不变号）==", flush=True)
    for topk, nd, tag in ((50, 1, "n_drop=1"), (50, 3, "n_drop=3"), (40, 2, "topk=40"), (60, 2, "topk=60")):
        _, net = run(topk, nd, 1_000_000)
        v = net.mean() * ANN
        print(f"  {tag}: 净={v:+.2%}  {'OK' if v > 0 else 'FLIP!'}", flush=True)

    # 科目③ 成本翻倍
    print("== 科目③ 成本翻倍（净保留>60%）==", flush=True)
    _, net = run(50, 2, 1_000_000, cost_mult=2.0)
    v = net.mean() * ANN
    print(f"  双倍成本净={v:+.2%}  保留率={v/BASE_NET:.0%}  {'PASS' if v / BASE_NET > 0.6 else 'FAIL'}", flush=True)

    # 科目④ 5万真实资金
    print("== 科目④ 5万真实资金复测 ==", flush=True)
    report, net = run(50, 2, 50_000)
    v = net.mean() * ANN
    print(f"  5万账户净={v:+.2%}  换手={report['turnover'].mean():.1%}  {'PASS' if v > 0.10 else 'FAIL(需评估topk是否过大)'}", flush=True)

    from exp_mlflow_log import log_experiment

    rid = log_experiment(
        "finalgate_ndrop2_open_exec",
        params={"candidate": "open_exec+ndrop2+5dlabel+rolling", "impact": 0.001},
        metrics={"net_base": BASE_NET, "net_cost2x": float(v)},
    )
    print(f"mlflow run: {rid}", flush=True)


if __name__ == "__main__":
    main()

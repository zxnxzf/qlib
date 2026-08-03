#!/usr/bin/env python3
"""实验#4：多LGB种子集成（ensemble3_rolling，重训级夜跑）

机制：3 个不同随机种子的 LGB 每季度各训一个，预测取平均——集成降方差，
直击 IR 诊断的"方差病"（收益集中20天、季度间剧烈摆动）。
对照：单模型 candidate1_pred（同滚动框架），组合口径 topk50/n_drop2/开盘/impact0.001/100万，
基线 IR=0.72（短窗）。预期：IR +0.1~0.3，毛不降。
"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/Users/bytedance/code/qlib")
sys.path.insert(0, "/Users/bytedance/code/qlib/my/scripts")

import lightgbm as lgb
import pandas as pd
import qlib
from qlib.contrib.data.handler import Alpha158
from qlib.data.dataset.handler import DataHandlerLP
from qlib.contrib.evaluate import backtest_daily

ART = Path("/Users/bytedance/code/qlib/my/artifacts")
NAME = "ensemble3b_rolling"
ANN = 238
BASE = dict(
    objective="mse", colsample_bytree=0.8879, learning_rate=0.0421, subsample=0.8789,
    lambda_l1=205.6999, lambda_l2=580.9768, max_depth=8, num_leaves=210, num_threads=8, verbosity=-1,
)
SEEDS = (11, 42, 2026)


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    t0 = time.time()
    handler = Alpha158(
        instruments="all_no_bj", start_time="2019-07-01", end_time="2026-07-28",
        fit_start_time="2019-07-01", fit_end_time="2022-12-31",
        label=["Ref($close, -6) / Ref($close, -1) - 1"],
    )
    learn = handler.fetch(col_set=["feature", "label"], data_key=DataHandlerLP.DK_L)
    infer = handler.fetch(col_set="feature", data_key=DataHandlerLP.DK_I)
    feat_cols = [c for c in learn.columns if c[0] == "feature"]
    label_col = [c for c in learn.columns if c[0] == "label"][0]
    print(f"learn={learn.shape} 耗时{(time.time()-t0)/60:.1f}min", flush=True)

    qstarts = list(pd.date_range("2023-01-01", "2026-07-01", freq="QS"))
    preds = []
    for i, q in enumerate(qstarts):
        q_end = qstarts[i + 1] - pd.Timedelta(days=1) if i + 1 < len(qstarts) else pd.Timestamp("2026-07-28")
        tr_s, tr_e = q - pd.DateOffset(months=42), q - pd.DateOffset(months=6)
        va_s, va_e = tr_e, q - pd.Timedelta(days=7)
        dts = learn.index.get_level_values("datetime")
        tr = learn[(dts >= tr_s) & (dts < tr_e)]
        tr = tr[tr[label_col].notna()]
        va = learn[(dts >= va_s) & (dts < va_e)]
        va = va[va[label_col].notna()]
        idts = infer.index.get_level_values("datetime")
        te = infer[(idts >= q) & (idts <= q_end)]
        dtrain = lgb.Dataset(tr[feat_cols].values, label=tr[label_col].values, free_raw_data=False)
        dvalid = lgb.Dataset(va[feat_cols].values, label=va[label_col].values, reference=dtrain, free_raw_data=False)
        qp = []
        for seed in SEEDS:
            params = {**BASE, "seed": seed, "feature_fraction_seed": seed}  # 仅特征采样种子差异，不激活bagging（与单模型训练方式严格一致）
            model = lgb.train(params, dtrain, num_boost_round=2000, valid_sets=[dvalid],
                              callbacks=[lgb.early_stopping(100, verbose=False)])
            qp.append(pd.Series(model.predict(te.values), index=te.index))
        avg = pd.concat(qp, axis=1).mean(axis=1).rename("score")
        preds.append(avg)
        print(f"{q.date()} 3seeds done 累计{(time.time()-t0)/60:.1f}min", flush=True)

    pred_df = pd.concat(preds).to_frame("score").sort_index()
    pred_df.to_pickle(ART / f"exp_{NAME}_pred.pkl")

    strategy = {"class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy",
                "kwargs": {"signal": pred_df, "topk": 50, "n_drop": 2, "hold_thresh": 1, "only_tradable": True}}
    report, _ = backtest_daily(
        start_time="2023-01-01", end_time="2026-07-28", strategy=strategy,
        account=1_000_000, benchmark="SH000300",
        exchange_kwargs={"deal_price": "open",
                         "limit_threshold": ("$open/Ref($close,1)-1 > 0.095", "$open/Ref($close,1)-1 < -0.095"),
                         "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5, "impact_cost": 0.001},
    )
    report.to_pickle(ART / f"exp_{NAME}_report.pkl")
    gross = report["return"] - report["bench"]
    net = report["return"] - report["cost"] - report["bench"]
    ir = net.mean() / net.std() * ANN ** 0.5
    print(f"集成版: 毛={gross.mean()*ANN:+.2%} 净={net.mean()*ANN:+.2%} IR={ir:+.2f}  (对照单模型: 毛+17.79% 净+13.37% IR+0.72)", flush=True)
    for y, g in net.groupby(net.index.year):
        print(f"  净 {y}: {g.mean()*ANN:+.1%}", flush=True)

    subprocess.run(f"/Users/bytedance/code/qlib/.venv/bin/python /Users/bytedance/code/qlib/my/scripts/package_dashboard.py {NAME} {ART/f'exp_{NAME}_pred.pkl'} {ART/f'exp_{NAME}_report.pkl'}", shell=True, check=False)
    from exp_mlflow_log import log_experiment
    rid = log_experiment(NAME, params={"seeds": str(SEEDS), "base": "candidate rolling", "portfolio": "topk50 n2 open impact0.001 1M"},
                         metrics={"gross_excess_ann": gross.mean()*ANN, "net_excess_ann": net.mean()*ANN, "ir_net": ir})
    print(f"mlflow run: {rid}", flush=True)


if __name__ == "__main__":
    main()

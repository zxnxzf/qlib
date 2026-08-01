#!/usr/bin/env python3
"""决赛长窗加测：候选（开盘执行+n_drop2+5日label+季度滚动）在 2021-01~2026-07 的表现。

滚动方案同 candidate1_rolling.py（train=q-42m~q-6m, valid=q-6m~q-7d），
季度从 2021Q1 到 2026Q3 共 23 个模型；handler 起点 2017-07。
组合口径：topk50/n_drop2/hold1/only_tradable/开盘执行/impact 0.001。
意义：2021-2022 从未参与任何设计决策，是当前最干净的"准样本外"段。
"""

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
NAME = "longwindow_ndrop2_open_exec"
ANN = 238
LGB_PARAMS = dict(
    objective="mse", colsample_bytree=0.8879, learning_rate=0.0421, subsample=0.8789,
    lambda_l1=205.6999, lambda_l2=580.9768, max_depth=8, num_leaves=210, num_threads=8, verbosity=-1,
)


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    t0 = time.time()
    handler = Alpha158(
        instruments="all_no_bj",
        start_time="2017-07-01",
        end_time="2026-07-28",
        fit_start_time="2017-07-01",
        fit_end_time="2020-12-31",
        label=["Ref($close, -6) / Ref($close, -1) - 1"],
    )
    learn = handler.fetch(col_set=["feature", "label"], data_key=DataHandlerLP.DK_L)
    infer = handler.fetch(col_set="feature", data_key=DataHandlerLP.DK_I)
    feat_cols = [c for c in learn.columns if c[0] == "feature"]
    label_col = [c for c in learn.columns if c[0] == "label"][0]
    print(f"learn={learn.shape} 耗时{(time.time()-t0)/60:.1f}min", flush=True)

    qstarts = list(pd.date_range("2021-01-01", "2026-07-01", freq="QS"))
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
        dtrain = lgb.Dataset(tr[feat_cols].values, label=tr[label_col].values, free_raw_data=True)
        dvalid = lgb.Dataset(va[feat_cols].values, label=va[label_col].values, reference=dtrain, free_raw_data=True)
        model = lgb.train(LGB_PARAMS, dtrain, num_boost_round=2000, valid_sets=[dvalid],
                          callbacks=[lgb.early_stopping(100, verbose=False)])
        preds.append(pd.Series(model.predict(te.values), index=te.index, name="score"))
        print(f"{q.date()} best_iter={model.best_iteration} 累计{(time.time()-t0)/60:.1f}min", flush=True)

    pred_df = pd.concat(preds).to_frame("score").sort_index()
    pred_df.to_pickle(ART / f"exp_{NAME}_pred.pkl")

    strategy = {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy",
        "kwargs": {"signal": pred_df, "topk": 50, "n_drop": 2, "hold_thresh": 1, "only_tradable": True},
    }
    report, _ = backtest_daily(
        start_time="2021-01-01", end_time="2026-07-28", strategy=strategy,
        account=1_000_000, benchmark="SH000300",
        exchange_kwargs={
            "deal_price": "open",
            "limit_threshold": ("$open/Ref($close,1)-1 > 0.095", "$open/Ref($close,1)-1 < -0.095"),
            "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5, "impact_cost": 0.001,
        },
    )
    report.to_pickle(ART / f"exp_{NAME}_report.pkl")
    net = report["return"] - report["cost"] - report["bench"]
    gross = report["return"] - report["bench"]
    print(f"长窗全期: 毛={gross.mean()*ANN:+.2%} 净={net.mean()*ANN:+.2%} IR={net.mean()/net.std()*ANN**0.5:+.2f}", flush=True)
    for y, g in net.groupby(net.index.year):
        print(f"  净 {y}: {g.mean()*ANN:+.1%}", flush=True)
    print(f"  其中 2021-2022（最干净准样本外段）: 净={net[net.index < '2023-01-01'].mean()*ANN:+.2%}", flush=True)

    from exp_mlflow_log import log_experiment

    rid = log_experiment(
        NAME,
        params={"candidate": "open_exec+ndrop2+5dlabel+rolling", "window": "2021-01~2026-07", "impact": 0.001},
        metrics={
            "gross_excess_ann": gross.mean() * ANN,
            "net_excess_ann": net.mean() * ANN,
            "ir_net": net.mean() / net.std() * ANN ** 0.5,
            "net_2021_2022": net[net.index < "2023-01-01"].mean() * ANN,
        },
    )
    print(f"mlflow run: {rid}", flush=True)


if __name__ == "__main__":
    main()

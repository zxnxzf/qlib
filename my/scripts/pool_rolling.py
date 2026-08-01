#!/usr/bin/env python3
"""票池对比轮：csi300 / csi500 池的 5日label + 季度滚动重训 + 回测。

用法: python pool_rolling.py csi300  （或 csi500）
方案与 candidate1_rolling.py 完全一致，仅票池不同；组合层加 only_tradable=True。
"""

import sys
import time

sys.path.insert(0, "/Users/bytedance/code/qlib")
import lightgbm as lgb
import pandas as pd
import qlib
from qlib.contrib.data.handler import Alpha158
from qlib.data.dataset.handler import DataHandlerLP
from qlib.contrib.evaluate import backtest_daily

ANN = 238
LGB_PARAMS = dict(
    objective="mse",
    colsample_bytree=0.8879,
    learning_rate=0.0421,
    subsample=0.8789,
    lambda_l1=205.6999,
    lambda_l2=580.9768,
    max_depth=8,
    num_leaves=210,
    num_threads=8,
    verbosity=-1,
)


def main(pool: str):
    out_pred = f"/Users/bytedance/code/qlib/my/artifacts/pool_{pool}_pred.pkl"
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)

    t0 = time.time()
    print(f"[1/3] handler ({pool}, 2019-07~2026-07, 5日label)...", flush=True)
    handler = Alpha158(
        instruments=pool,
        start_time="2019-07-01",
        end_time="2026-07-28",
        fit_start_time="2019-07-01",
        fit_end_time="2022-12-31",
        label=["Ref($close, -6) / Ref($close, -1) - 1"],
    )
    learn = handler.fetch(col_set=["feature", "label"], data_key=DataHandlerLP.DK_L)
    infer = handler.fetch(col_set="feature", data_key=DataHandlerLP.DK_I)
    feat_cols = [c for c in learn.columns if c[0] == "feature"]
    label_col = [c for c in learn.columns if c[0] == "label"][0]
    print(f"    learn={learn.shape} 耗时{(time.time()-t0)/60:.1f}min", flush=True)

    print("[2/3] 季度滚动训练...", flush=True)
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
        dtrain = lgb.Dataset(tr[feat_cols].values, label=tr[label_col].values, free_raw_data=True)
        dvalid = lgb.Dataset(va[feat_cols].values, label=va[label_col].values, reference=dtrain, free_raw_data=True)
        model = lgb.train(LGB_PARAMS, dtrain, num_boost_round=2000, valid_sets=[dvalid],
                          callbacks=[lgb.early_stopping(100, verbose=False)])
        preds.append(pd.Series(model.predict(te.values), index=te.index, name="score"))
        print(f"    {q.date()} best_iter={model.best_iteration} 累计{(time.time()-t0)/60:.1f}min", flush=True)

    pred_df = pd.concat(preds).to_frame("score").sort_index()
    pred_df.to_pickle(out_pred)

    print("[3/3] 回测...", flush=True)
    strategy = {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy",
        "kwargs": {"signal": pred_df, "topk": 50, "n_drop": 10, "hold_thresh": 1, "only_tradable": True},
    }
    report, _ = backtest_daily(
        start_time="2023-01-01", end_time="2026-07-28", strategy=strategy,
        account=1_000_000, benchmark="SH000300",
        exchange_kwargs={"limit_threshold": 0.095, "deal_price": "close",
                         "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5},
    )
    report.to_pickle(out_pred.replace("_pred", "_report"))
    ex = report["return"] - report["cost"] - report["bench"]
    print(f"== {pool} 含成本超额 vs 沪深300 ==", flush=True)
    ir = ex.mean() / ex.std() * ANN ** 0.5
    print(f"  全期={ex.mean()*ANN:+.2%} IR={ir:+.2f}", flush=True)
    for y, g in ex.groupby(ex.index.year):
        print(f"    {y}={g.mean()*ANN:+.2%}", flush=True)
    print(f"  25-08后={ex[ex.index>='2025-08-01'].mean()*ANN:+.2%}  日均换手={report['turnover'].mean():.1%}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "csi300")

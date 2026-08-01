#!/usr/bin/env python3
"""候选一号：5日label + 季度滚动重训 + 快换手(n_drop=10)。

滚动方案（每季度 q）：
  train = [q-42个月, q-6个月)   valid = [q-6个月, q-7天)   predict = [q, 下一季度)
特征/label 由同一个 Alpha158 handler 一次算好（CS 标签归一化是逐日截面操作，无时间泄漏），
每季度切片后用 lightgbm 原生接口训练（参数与现役一致），预测拼接后统一回测。
"""

import sys
import time

sys.path.insert(0, "/Users/bytedance/code/qlib")
import lightgbm as lgb
import numpy as np
import pandas as pd
import qlib
from qlib.contrib.data.handler import Alpha158
from qlib.data.dataset.handler import DataHandlerLP
from qlib.contrib.evaluate import backtest_daily

OUT = "/Users/bytedance/code/qlib/my/artifacts/candidate1_pred.pkl"
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


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)

    t0 = time.time()
    print("[1/3] 构建 Alpha158 handler (2019-07 ~ 2026-07, 5日label)...", flush=True)
    handler = Alpha158(
        instruments="all_no_bj",
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
    print(f"    learn={learn.shape} infer={infer.shape} 耗时{(time.time()-t0)/60:.1f}min", flush=True)

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
        model = lgb.train(
            LGB_PARAMS,
            dtrain,
            num_boost_round=2000,
            valid_sets=[dvalid],
            callbacks=[lgb.early_stopping(100, verbose=False)],
        )
        score = pd.Series(model.predict(te.values), index=te.index, name="score")
        preds.append(score)
        print(
            f"    {q.date()} ~ {q_end.date()} | train={len(tr):,} valid={len(va):,} "
            f"best_iter={model.best_iteration} pred_days={te.index.get_level_values(0).nunique()} "
            f"累计{(time.time()-t0)/60:.1f}min",
            flush=True,
        )

    pred_df = pd.concat(preds).to_frame("score").sort_index()
    pred_df.to_pickle(OUT)
    print(f"    预测已保存: {OUT} shape={pred_df.shape}", flush=True)

    print("[3/3] 回测 2023-01 ~ 2026-07 (topk=50, n_drop=10, hold=1, 账户100万)...", flush=True)
    strategy = {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy",
        "kwargs": {"signal": pred_df, "topk": 50, "n_drop": 10, "hold_thresh": 1},
    }
    report, _ = backtest_daily(
        start_time="2023-01-01",
        end_time="2026-07-28",
        strategy=strategy,
        account=1_000_000,
        benchmark="SH000300",
        exchange_kwargs={
            "limit_threshold": 0.095,
            "deal_price": "close",
            "open_cost": 0.0005,
            "close_cost": 0.0015,
            "min_cost": 5,
        },
    )
    report.to_pickle(OUT.replace("_pred.pkl", "_report.pkl"))
    ex = report["return"] - report["cost"] - report["bench"]

    def stat(s, label):
        ir = s.mean() / s.std() * ANN ** 0.5 if s.std() > 0 else float("nan")
        cum = (1 + s).cumprod()
        mdd = (cum / cum.cummax() - 1).min()
        print(f"  {label:16s} 超额年化={s.mean()*ANN:+.2%} IR={ir:+.2f} 超额回撤={mdd:.1%}", flush=True)

    print("== 候选一号 含成本超额（vs 沪深300）==", flush=True)
    stat(ex, "全期 23-01~26-07")
    for y, g in ex.groupby(ex.index.year):
        stat(g, f"  {y} 年")
    stat(ex[ex.index < "2025-08-01"], "  23-01~25-07")
    stat(ex[ex.index >= "2025-08-01"], "  25-08后(已烧段)")
    print(f"  日均换手={report['turnover'].mean():.1%}", flush=True)


if __name__ == "__main__":
    main()

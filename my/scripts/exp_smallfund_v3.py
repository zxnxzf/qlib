#!/usr/bin/env python3
"""实验：smallfund_v3——受限票池内专属训练（小资金线，重训级）

受限池：未复权价 2~20 元 且 20日均成交额截面前 70%（每日动态，约 2200 只）。
训练与预测都只在受限池内进行（对照 v2：全市场训练+受限入组，毛≈0）。
滚动方案同主线（季度重训、训练窗3年）；组合：5万、topk25、n_drop2、开盘执行、impact 0.001。
成败判据：受限池内 RankIC≥0.03 且毛超额转正；不达则小资金方向宣告需换资产类别。
注：label 的截面标准化沿用全市场口径（逐日线性变换不改变池内排序，近似可接受）。
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
NAME = "smallfund_v3"
ANN = 238
LGB_PARAMS = dict(
    objective="mse", colsample_bytree=0.8879, learning_rate=0.0421, subsample=0.8789,
    lambda_l1=205.6999, lambda_l2=580.9768, max_depth=8, num_leaves=210, num_threads=8, verbosity=-1,
)


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    t0 = time.time()
    from qlib.data import D

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

    inst = D.instruments("all_no_bj")
    mask_feats = D.features(inst, ["Mean($amount, 20)", "$close/$factor"], start_time="2019-07-01", end_time="2026-07-28")
    mask_feats.columns = ["amt20", "raw_close"]
    mask_feats["amt_rank"] = mask_feats.groupby(level="datetime")["amt20"].rank(pct=True)
    pool_ok = (mask_feats["raw_close"] >= 2.0) & (mask_feats["raw_close"] <= 20.0) & (mask_feats["amt_rank"] >= 0.30)
    pool_ok = pool_ok.rename("pool_ok")

    # 掩码索引为(instrument,datetime)，特征表为(datetime,instrument)且列是两层——用换序 reindex 对齐
    pool_idx = pool_ok.swaplevel()
    pool_idx.index.names = learn.index.names
    learn = learn[pool_idx.reindex(learn.index).fillna(False).values]
    infer = infer[pool_idx.reindex(infer.index).fillna(False).values]
    print(f"受限池 learn={learn.shape} infer={infer.shape} 耗时{(time.time()-t0)/60:.1f}min", flush=True)

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
        print(f"{q.date()} train={len(tr):,} best_iter={model.best_iteration} 累计{(time.time()-t0)/60:.1f}min", flush=True)

    pred_df = pd.concat(preds).to_frame("score").sort_index()
    pred_df.to_pickle(ART / f"exp_{NAME}_pred.pkl")

    # 海选：池内 IC / RankIC
    label = D.features(inst, ["Ref($close, -6) / Ref($close, -1) - 1"], start_time="2023-01-01", end_time="2026-07-28")
    label.columns = ["LABEL0"]
    dfl = pred_df.join(label, how="inner").dropna()
    ic = dfl.groupby(level="datetime").apply(lambda g: g["score"].corr(g["LABEL0"]))
    ric = dfl.groupby(level="datetime").apply(lambda g: g["score"].corr(g["LABEL0"], method="spearman"))
    print(f"池内 IC={ic.mean():.4f} RankIC={ric.mean():.4f}  海选线0.03", flush=True)

    strategy = {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy",
        "kwargs": {"signal": pred_df, "topk": 25, "n_drop": 2, "hold_thresh": 1, "only_tradable": True},
    }
    report, _ = backtest_daily(
        start_time="2023-01-01", end_time="2026-07-28", strategy=strategy,
        account=50_000, benchmark="SH000300",
        exchange_kwargs={
            "deal_price": "open",
            "limit_threshold": ("$open/Ref($close,1)-1 > 0.095", "$open/Ref($close,1)-1 < -0.095"),
            "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5, "impact_cost": 0.001,
        },
    )
    report.to_pickle(ART / f"exp_{NAME}_report.pkl")
    gross = report["return"] - report["bench"]
    net = report["return"] - report["cost"] - report["bench"]
    print(
        f"组合(5万,topk25,n2): 毛={gross.mean()*ANN:+.2%} 净={net.mean()*ANN:+.2%} "
        f"IR={net.mean()/net.std()*ANN**0.5:+.2f} 账户终值={report['account'].iloc[-1]:,.0f} 最低={report['account'].min():,.0f}",
        flush=True,
    )
    for y, g in net.groupby(net.index.year):
        print(f"  净 {y}: {g.mean()*ANN:+.1%}", flush=True)

    subprocess.run(
        f"/Users/bytedance/code/qlib/.venv/bin/python /Users/bytedance/code/qlib/my/scripts/package_dashboard.py "
        f"{NAME} {ART / f'exp_{NAME}_pred.pkl'} {ART / f'exp_{NAME}_report.pkl'}",
        shell=True, check=False,
    )

    from exp_mlflow_log import log_experiment

    rid = log_experiment(
        NAME,
        params={"pool": "price2-20&amt_rank>=0.3", "dedicated_training": True, "account": 50000,
                "topk": 25, "n_drop": 2, "control": "smallfund_v2(全市场训练)"},
        metrics={
            "ic": ic.mean(), "rank_ic": ric.mean(),
            "gross_excess_ann": gross.mean() * ANN, "net_excess_ann": net.mean() * ANN,
            "account_min": float(report["account"].min()),
        },
        dashboard=str(ART / "faux_recorders" / NAME / "recorder_dashboard.html"),
    )
    print(f"mlflow run: {rid}", flush=True)


if __name__ == "__main__":
    main()

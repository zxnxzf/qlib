"""信号层：季度滚动 LGB 模型的训练/加载 + 每日打分。

与研究流水线（candidate1_rolling）同口径：train=[q-42m, q-6m)，valid=[q-6m, q-7d)，
Alpha158 特征 + 5日label（截面标准化）。模型按季度落盘复用。
"""

from pathlib import Path
from typing import Optional

import lightgbm as lgb
import pandas as pd

from . import config as C
from . import data


def quarter_start(date: str) -> pd.Timestamp:
    ts = pd.Timestamp(date)
    return pd.Timestamp(ts.year, (ts.quarter - 1) * 3 + 1, 1)


def model_path(q: pd.Timestamp) -> Path:
    return C.MODEL_DIR / f"{q.year}Q{q.quarter}.txt"


def _build_handler(start: str, end: str):
    from qlib.contrib.data.handler import Alpha158

    data.init_qlib()
    return Alpha158(
        instruments=C.POOL, start_time=start, end_time=end,
        fit_start_time=start, fit_end_time=end,
        label=[C.LABEL_EXPR],
    )


def ensure_model(for_date: str, log=print) -> Path:
    """确保 for_date 所属季度的模型存在；缺则训练（分钟级~小时级）。"""
    q = quarter_start(for_date)
    mp = model_path(q)
    if mp.exists():
        return mp
    C.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    tr_s = (q - pd.DateOffset(months=C.TRAIN_MONTHS)).strftime("%Y-%m-%d")
    tr_e = q - pd.DateOffset(months=C.VALID_MONTHS)
    va_e = q - pd.Timedelta(days=C.VALID_GAP_DAYS)
    log(f"[signal] 训练 {q.year}Q{q.quarter} 模型: train {tr_s}~{tr_e.date()}, valid ~{va_e.date()}")

    from qlib.data.dataset.handler import DataHandlerLP

    handler = _build_handler(tr_s, va_e.strftime("%Y-%m-%d"))
    learn = handler.fetch(col_set=["feature", "label"], data_key=DataHandlerLP.DK_L)
    feat_cols = [c for c in learn.columns if c[0] == "feature"]
    label_col = [c for c in learn.columns if c[0] == "label"][0]
    dts = learn.index.get_level_values("datetime")
    tr = learn[dts < tr_e]
    tr = tr[tr[label_col].notna()]
    va = learn[(dts >= tr_e) & (dts <= va_e)]
    va = va[va[label_col].notna()]
    dtrain = lgb.Dataset(tr[feat_cols].values, label=tr[label_col].values)
    dvalid = lgb.Dataset(va[feat_cols].values, label=va[label_col].values, reference=dtrain)
    model = lgb.train(
        C.LGB_PARAMS, dtrain, num_boost_round=C.NUM_BOOST_ROUND, valid_sets=[dvalid],
        callbacks=[lgb.early_stopping(C.EARLY_STOP, verbose=False)],
    )
    model.save_model(str(mp), num_iteration=model.best_iteration)
    log(f"[signal] 模型已保存 {mp.name} (best_iter={model.best_iteration}, train={len(tr):,})")
    return mp


def scores_for(date: str, log=print) -> pd.Series:
    """对 date 当天全票池打分（使用该日所属季度的模型）。index=instrument。"""
    mp = ensure_model(date, log=log)
    booster = lgb.Booster(model_file=str(mp))
    # 特征窗口：Alpha158 最长回看 60 交易日，留足余量取 400 自然日
    start = (pd.Timestamp(date) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    handler = _build_handler(start, date)
    from qlib.data.dataset.handler import DataHandlerLP

    infer = handler.fetch(col_set="feature", data_key=DataHandlerLP.DK_I)
    dts = infer.index.get_level_values("datetime")
    day = infer[dts == pd.Timestamp(date)]
    if day.empty:
        raise RuntimeError(f"{date} 无特征数据")
    scores = pd.Series(booster.predict(day.values), index=day.index.get_level_values("instrument"), name="score")
    log(f"[signal] {date} 打分 {len(scores)} 只（模型 {mp.stem}）")
    return scores

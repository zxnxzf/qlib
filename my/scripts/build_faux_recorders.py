#!/usr/bin/env python3
"""把 backtest_daily 直跑的实验（pred.pkl + report.pkl）打包成标准 recorder artifacts 结构，
供 examples/custom/recorder_visualizer_from_path.py 使用。

生成目录: my/artifacts/faux_recorders/<name>/artifacts/
  ├── pred.pkl
  ├── portfolio_analysis/report_normal_1day.pkl, port_analysis_1day.pkl
  ├── sig_analysis/ic.pkl, ric.pkl        （vs 5日label）
  └── ../metrics/IC, ICIR, Rank IC, ...   （mlflow 格式）
"""

import sys
from pathlib import Path

sys.path.insert(0, "/Users/bytedance/code/qlib")
import pandas as pd
import qlib
from qlib.data import D
from qlib.contrib.evaluate import risk_analysis

T = Path("/Users/bytedance/code/qlib/my/artifacts")
CONFIGS = {
    "all_no_bj_rolling": (T / "candidate1_pred.pkl", T / "candidate1_report_tradable.pkl"),
    "csi300_rolling": (T / "pool_csi300_pred.pkl", T / "pool_csi300_report.pkl"),
    "csi500_rolling": (T / "pool_csi500_pred.pkl", T / "pool_csi500_report.pkl"),
    "filtered_rolling": (T / "candidate1_pred.pkl", T / "pool_filtered_report.pkl"),
}


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    inst = D.instruments("all_no_bj")
    label = D.features(inst, ["Ref($close, -6) / Ref($close, -1) - 1"], start_time="2023-01-01", end_time="2026-07-28")
    label.columns = ["label"]

    for name, (pred_path, report_path) in CONFIGS.items():
        root = T / "faux_recorders" / name
        art = root / "artifacts"
        (art / "portfolio_analysis").mkdir(parents=True, exist_ok=True)
        (art / "sig_analysis").mkdir(parents=True, exist_ok=True)
        (root / "metrics").mkdir(parents=True, exist_ok=True)

        pred = pd.read_pickle(pred_path)
        report = pd.read_pickle(report_path)
        pred.to_pickle(art / "pred.pkl")
        report.to_pickle(art / "portfolio_analysis" / "report_normal_1day.pkl")
        # label.pkl：visualizer 的 IC 图用 pred+label 现场算（与 SignalRecord 同构）
        label_out = pred.join(label.rename(columns={"label": "LABEL0"}), how="inner")[["LABEL0"]].dropna()
        label_out.to_pickle(art / "label.pkl")

        # port_analysis（与 PortAnaRecord 同构）
        analysis = {
            "excess_return_without_cost": risk_analysis(report["return"] - report["bench"], freq="day"),
            "excess_return_with_cost": risk_analysis(report["return"] - report["cost"] - report["bench"], freq="day"),
        }
        analysis_df = pd.concat(analysis)
        analysis_df.to_pickle(art / "portfolio_analysis" / "port_analysis_1day.pkl")

        # IC / RankIC（vs 5日label，与训练目标一致）
        df = pred.join(label, how="inner").dropna()
        ic = df.groupby(level="datetime").apply(lambda g: g["score"].corr(g["label"]))
        ric = df.groupby(level="datetime").apply(lambda g: g["score"].corr(g["label"], method="spearman"))
        ic.to_pickle(art / "sig_analysis" / "ic.pkl")
        ric.to_pickle(art / "sig_analysis" / "ric.pkl")

        metrics = {
            "IC": ic.mean(),
            "ICIR": ic.mean() / ic.std(),
            "Rank IC": ric.mean(),
            "Rank ICIR": ric.mean() / ric.std(),
            "1day.excess_return_with_cost.annualized_return": (report["return"] - report["cost"] - report["bench"]).mean() * 238,
            "1day.excess_return_with_cost.information_ratio":
                (report["return"] - report["cost"] - report["bench"]).mean()
                / (report["return"] - report["cost"] - report["bench"]).std() * 238 ** 0.5,
        }
        for k, v in metrics.items():
            (root / "metrics" / k).write_text(f"0 {v} 0\n")

        print(f"{name}: IC={metrics['IC']:.4f} RankIC={metrics['Rank IC']:.4f} -> {art}")


if __name__ == "__main__":
    main()

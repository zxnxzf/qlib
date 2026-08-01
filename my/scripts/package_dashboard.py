#!/usr/bin/env python3
"""把任意实验的 (pred.pkl, report.pkl) 打包成 faux recorder 并生成用户可视化仪表板。

用法:
  .venv/bin/python my/scripts/package_dashboard.py <名字> <pred.pkl路径> <report.pkl路径>
产出:
  my/artifacts/faux_recorders/<名字>/recorder_dashboard.html
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/Users/bytedance/code/qlib")

import pandas as pd
import qlib
from qlib.data import D
from qlib.contrib.evaluate import risk_analysis

ART = Path("/Users/bytedance/code/qlib/my/artifacts")
ANN = 238


def main(name: str, pred_path: str, report_path: str) -> None:
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle(pred_path)
    report = pd.read_pickle(report_path)
    gross = report["return"] - report["bench"]
    net = report["return"] - report["cost"] - report["bench"]

    root = ART / "faux_recorders" / name
    art = root / "artifacts"
    (art / "portfolio_analysis").mkdir(parents=True, exist_ok=True)
    (art / "sig_analysis").mkdir(parents=True, exist_ok=True)
    (root / "metrics").mkdir(parents=True, exist_ok=True)
    pred.to_pickle(art / "pred.pkl")
    report.to_pickle(art / "portfolio_analysis" / "report_normal_1day.pkl")
    pd.concat(
        {
            "excess_return_without_cost": risk_analysis(gross, freq="day"),
            "excess_return_with_cost": risk_analysis(net, freq="day"),
        }
    ).to_pickle(art / "portfolio_analysis" / "port_analysis_1day.pkl")

    start = str(report.index.min().date())
    end = str(report.index.max().date())
    inst = D.instruments("all_no_bj")
    label = D.features(inst, ["Ref($close, -6) / Ref($close, -1) - 1"], start_time=start, end_time=end)
    label.columns = ["LABEL0"]
    pred.join(label, how="inner")[["LABEL0"]].dropna().to_pickle(art / "label.pkl")
    dfl = pred.join(label, how="inner").dropna()
    ic = dfl.groupby(level="datetime").apply(lambda g: g["score"].corr(g["LABEL0"]))
    ric = dfl.groupby(level="datetime").apply(lambda g: g["score"].corr(g["LABEL0"], method="spearman"))
    ic.to_pickle(art / "sig_analysis" / "ic.pkl")
    ric.to_pickle(art / "sig_analysis" / "ric.pkl")
    for k, v in {
        "IC": ic.mean(),
        "ICIR": ic.mean() / ic.std(),
        "Rank IC": ric.mean(),
        "Rank ICIR": ric.mean() / ric.std(),
        "1day.excess_return_with_cost.annualized_return": net.mean() * ANN,
        "1day.excess_return_with_cost.information_ratio": net.mean() / net.std() * ANN ** 0.5,
    }.items():
        (root / "metrics" / k).write_text(f"0 {v} 0\n")

    subprocess.run(
        f'cd "{root}" && echo "{art}" | /Users/bytedance/code/qlib/.venv/bin/python '
        f"/Users/bytedance/code/qlib/my/scripts/recorder_visualizer_from_path.py > /dev/null 2>&1",
        shell=True,
        check=False,
    )
    print(f"dashboard: {root / 'recorder_dashboard.html'}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])

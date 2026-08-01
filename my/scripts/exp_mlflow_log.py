#!/usr/bin/env python3
"""实验结果统一落档到 my/mlruns（qlib/mlflow 原生格式，实验名=想法名）。

用法（脚本内调用或命令行）：
  from exp_mlflow_log import log_experiment
  log_experiment(
      name="t1_open_price_exec",
      params={"pool": "all_no_bj", "label": "5d", "deal_price": "open"},
      metrics={"gross_excess_ann": 0.065, "net_excess_ann": -0.029, "ir": -0.12,
               "ic": 0.0715, "rank_ic": 0.0637},
      dashboard="my/artifacts/faux_recorders/xxx/recorder_dashboard.html",
  )
"""

import sys

MLRUNS_URI = "/Users/bytedance/code/qlib/my/mlruns"


def log_experiment(name: str, params: dict, metrics: dict, dashboard: str = "") -> str:
    import mlflow

    mlflow.set_tracking_uri(MLRUNS_URI)
    mlflow.set_experiment(name)
    with mlflow.start_run() as run:
        if dashboard:
            params = {**params, "dashboard": dashboard}
        mlflow.log_params(params)
        mlflow.log_metrics({k: float(v) for k, v in metrics.items()})
        return run.info.run_id


if __name__ == "__main__":
    # 自检：写一条测试记录再确认可读
    rid = log_experiment(
        "_selftest",
        params={"purpose": "smoke-test"},
        metrics={"dummy": 1.0},
    )
    import mlflow

    mlflow.set_tracking_uri(MLRUNS_URI)
    got = mlflow.get_run(rid)
    assert got.data.metrics.get("dummy") == 1.0
    print(f"selftest OK, run_id={rid}, experiment 已写入 {MLRUNS_URI}")
    sys.exit(0)

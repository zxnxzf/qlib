# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

# pylint: skip-file
# flake8: noqa

import os
import sys
import yaml
import json
import copy
import pickle
import logging
import importlib
import subprocess
import pandas as pd
import numpy as np

from abc import abstractmethod

from ...log import get_module_logger, TimeInspector
from hyperopt import fmin, tpe
from hyperopt import STATUS_OK, STATUS_FAIL


class Tuner:
    def __init__(self, tuner_config, optim_config):
        self.logger = get_module_logger("Tuner", level=logging.INFO)

        self.tuner_config = tuner_config
        self.optim_config = optim_config

        self.max_evals = self.tuner_config.get("max_evals", 10)
        self.ex_dir = os.path.join(
            self.tuner_config["experiment"]["dir"],
            self.tuner_config["experiment"]["name"],
        )

        self.best_params = None
        self.best_res = None
        self.best_run_id = None
        self.best_experiment_id = None
        self.best_metric = None

        self.space = self.setup_space()

    def tune(self):
        TimeInspector.set_time_mark()
        fmin(
            fn=self.objective,
            space=self.space,
            algo=tpe.suggest,
            max_evals=self.max_evals,
            show_progressbar=False,
        )
        self.logger.info("Local best params: {} ".format(self.best_params))
        TimeInspector.log_cost_time(
            "Finished searching best parameters in Tuner {}.".format(self.tuner_config["experiment"]["id"])
        )

        self.save_local_best_params()

    @abstractmethod
    def objective(self, params):
        """
        Implement this method to give an optimization factor using parameters in space.
        :return: {'loss': a factor for optimization, float type,
                  'status': the status of this evaluation step, STATUS_OK or STATUS_FAIL}.
        """
        pass

    @abstractmethod
    def setup_space(self):
        """
        Implement this method to setup the searching space of tuner.
        :return: searching space, dict type.
        """
        pass

    @abstractmethod
    def save_local_best_params(self):
        """
        Implement this method to save the best parameters of this tuner.
        """
        pass


class QLibTuner(Tuner):
    ESTIMATOR_CONFIG_NAME = "estimator_config.yaml"
    EXP_INFO_NAME = "exp_info.json"
    EXP_RESULT_DIR = "sacred/{}"
    EXP_RESULT_NAME = "analysis.pkl"
    LOCAL_BEST_PARAMS_NAME = "local_best_params.json"

    def objective(self, params):
        # 1. Setup an config for a specific estimator process
        estimator_path = self.setup_estimator_config(params)
        self.logger.info("Searching params: {} ".format(params))

        # 2. Use subprocess to do the estimator program, this process will wait until subprocess finish
        cmd = [sys.executable, "-m", "qlib.contrib.model.launcher", "-c", estimator_path]
        sub_fails = subprocess.call(cmd)
        if sub_fails:
            # If this subprocess failed, ignore this evaluation step
            self.logger.info("Estimator experiment failed when using this searching parameters")
            return {"loss": np.nan, "status": STATUS_FAIL}

        # 3. Fetch the result of subprocess, and check whether the result is Nan
        res = self.fetch_result()
        if np.isnan(res):
            status = STATUS_FAIL
        else:
            status = STATUS_OK

        # 4. Save the best score and params
        if status == STATUS_OK:
            if self.best_res is None or np.isnan(self.best_res) or self.best_res > res:
                self.best_res = res
                self.best_params = params
                self.best_run_id = getattr(self, "_last_run_id", None)
                self.best_experiment_id = getattr(self, "_last_experiment_id", None)
                self.best_metric = getattr(self, "_last_metric_raw", None)

        # 5. Return the result as optim objective
        return {"loss": res, "status": status}

    def fetch_result(self):
        # 1. Get experiment information
        exp_info_path = os.path.join(self.ex_dir, QLibTuner.EXP_INFO_NAME)
        with open(exp_info_path) as fp:
            exp_info = json.load(fp)
        estimator_ex_id = exp_info["id"]
        self._last_run_id = estimator_ex_id
        self._last_experiment_id = self._resolve_mlflow_experiment_id(estimator_ex_id)

        # 2. Get raw metric value (the larger/smaller direction is handled by `optim_type` below)
        if self.optim_config.report_type == "model":
            perf = exp_info.get("performance", {}) or {}
            if self.optim_config.report_factor == "model_score":
                raw_val = perf.get("model_score", np.nan)
                raw_val = np.mean(raw_val) if raw_val is not None else np.nan
            elif self.optim_config.report_factor == "model_pearsonr":
                raw_val = perf.get("model_pearsonr", np.nan)
            else:
                raw_val = np.nan
        else:
            exp_result_dir = os.path.join(self.ex_dir, QLibTuner.EXP_RESULT_DIR.format(estimator_ex_id))
            exp_result_path = os.path.join(exp_result_dir, QLibTuner.EXP_RESULT_NAME)
            with open(exp_result_path, "rb") as fp:
                analysis_df = pickle.load(fp)
            raw_val = analysis_df.loc[self.optim_config.report_type].loc[self.optim_config.report_factor].values[0]

        self._last_metric_raw = raw_val

        # 3. Convert raw metric to loss for Hyperopt (always minimize)
        if self.optim_config.optim_type == "min":
            return raw_val
        elif self.optim_config.optim_type == "max":
            return -raw_val
        else:
            # correlation: best value is 1 (e.g. Pearson correlation coefficient)
            return np.abs(raw_val - 1)

    def setup_estimator_config(self, params):
        estimator_config = copy.deepcopy(self.tuner_config)
        estimator_config["model"].update({"args": params["model_space"]})
        estimator_config["strategy"].update({"args": params["strategy_space"]})
        if params.get("data_label_space", None) is not None:
            estimator_config["data"]["args"].update(params["data_label_space"])

        estimator_path = os.path.join(
            self.tuner_config["experiment"].get("dir", "../"),
            QLibTuner.ESTIMATOR_CONFIG_NAME,
        )

        with open(estimator_path, "w") as fp:
            yaml.dump(estimator_config, fp)

        return estimator_path

    def setup_space(self):
        # 1. Setup model space
        model_space_name = self.tuner_config["model"].get("space", None)
        if model_space_name is None:
            raise ValueError("Please give the search space of model.")
        model_space = getattr(
            importlib.import_module(".space", package="qlib.contrib.tuner"),
            model_space_name,
        )

        # 2. Setup strategy space
        strategy_space_name = self.tuner_config["strategy"].get("space", None)
        if strategy_space_name is None:
            raise ValueError("Please give the search space of strategy.")
        strategy_space = getattr(
            importlib.import_module(".space", package="qlib.contrib.tuner"),
            strategy_space_name,
        )

        # 3. Setup data label space if given
        if self.tuner_config.get("data_label", None) is not None:
            data_label_space_name = self.tuner_config["data_label"].get("space", None)
            if data_label_space_name is not None:
                data_label_space = getattr(
                    importlib.import_module(".space", package="qlib.contrib.tuner"),
                    data_label_space_name,
                )
        else:
            data_label_space_name = None

        # 4. Combine the searching space
        space = dict()
        space.update({"model_space": model_space})
        space.update({"strategy_space": strategy_space})
        if data_label_space_name is not None:
            space.update({"data_label_space": data_label_space})

        return space

    def save_local_best_params(self):
        TimeInspector.set_time_mark()
        local_best_params_path = os.path.join(self.ex_dir, QLibTuner.LOCAL_BEST_PARAMS_NAME)
        with open(local_best_params_path, "w") as fp:
            json.dump(self.best_params, fp)
        TimeInspector.log_cost_time(
            "Finished saving local best tuner parameters to: {} .".format(local_best_params_path)
        )

        if self.best_res is None:
            self.logger.info("No valid trial results found.")
            return

        metric_name = f"{self.optim_config.report_type}.{self.optim_config.report_factor}"
        best_metric = self.best_metric
        is_nan = False
        try:
            is_nan = bool(np.isnan(best_metric))
        except TypeError:
            is_nan = False

        if best_metric is None or is_nan:
            self.logger.info(f"BEST RESULT: {metric_name} (loss)={self.best_res}, run_id={self.best_run_id}")
        else:
            self.logger.info(f"BEST RESULT: {metric_name}={best_metric}, run_id={self.best_run_id}")

        if self.best_run_id and self.best_experiment_id is not None:
            self.logger.info(
                f"BEST RUN: experiment_id={self.best_experiment_id}, recorder_id={self.best_run_id}"
            )

    def _resolve_mlflow_experiment_id(self, run_id):
        mlruns_dir = os.path.join(self.ex_dir, "mlruns")
        if not os.path.isdir(mlruns_dir):
            return None
        for exp_id in os.listdir(mlruns_dir):
            if exp_id.startswith("."):
                continue
            exp_dir = os.path.join(mlruns_dir, exp_id)
            if not os.path.isdir(exp_dir):
                continue
            if os.path.isdir(os.path.join(exp_dir, run_id)):
                return exp_id
        return None

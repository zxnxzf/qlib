# 自用问答记录（Self Q&A）

本文件用于记录个人在使用 Qlib 过程中的问题与简要解答，方便后续回顾。

> 记录日期：2025-12-27

## 1. 如何用 `qrun` 跑 LightGBM 示例？

- 在项目根目录完成环境准备（可选但推荐）：
  - `make prerequisite`：编译 `_libs`（新环境建议执行一次）
  - `make dev`：安装 `pyqlib` 及命令行入口 `qrun`
- 准备示例数据（A 股日频）：
  - `python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/cn_data --region cn --interval 1d`
- 运行 LightGBM Alpha158 Benchmark：
  - `cd examples`
  - `qrun benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_2020_2025.yaml`
- 如果命令行找不到 `qrun`，可以使用备用入口：
  - `python -m qlib.cli.run benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_2020_2025.yaml`

## 2. 基准的收益率是怎么算出来的？

- **直观理解**：好像买了一只「沪深 300 指数基金」一直拿着，看它每天账面涨跌多少，再总结出“一年大概能赚多少、波动多大、最大回撤多深”。
- **日收益的计算**：对基准（默认 `SH000300`），取每天收盘价，计算当天相对前一天的涨跌百分比：
  - `bench_return_t = close_t / close_{t-1} - 1`
- **表里的几个核心指标**（基于这串 `bench_return_t` 计算）：
  - `mean`：这段时间里**平均每天涨多少%**；
  - `std`：日收益的标准差，表示**每天波动有多大**；
  - `annualized_return`：假设未来每天都按这个平均节奏走，一年有约 238 个交易日，则
    - `annualized_return ≈ mean * 交易日数`，例如平均日收益约 0.07%，乘以 ~238 ≈ 17.4%；
  - `max_drawdown`：把每日收益**累加成一条曲线**，看这条曲线从历史最高点到最低点的最大跌幅，反映“最多一度回撤了多少”。

## 3. 回测过程中 `Mean of empty slice` 的告警怎么处理的？

- 问题现象：在回测进度条附近，经常刷出类似日志：
  - `RuntimeWarning: Mean of empty slice`
  - 来源：`qlib/utils/index_data.py` 中 `IndexData.mean` 调用 `np.nanmean`，在数据为空或全是 `nan` 时触发。
- 处理方式（不改变计算结果，只是静音告警）：
  - 文件：`qlib/utils/index_data.py`
  - 修改点：`IndexData.mean` 方法内部，使用 `warnings.catch_warnings()` 局部屏蔽这个运行时警告：
    ```python
    import warnings

    def mean(self, axis=None, dtype=None, out=None):
        assert out is None and dtype is None, "`out` is just for compatible with numpy's aggregating function"
        # FIXME: weird logic and not general
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            if axis is None:
                return np.nanmean(self.data)
            elif axis == 0:
                tmp_data = np.nanmean(self.data, axis=0)
                return SingleData(tmp_data, self.columns)
            elif axis == 1:
                tmp_data = np.nanmean(self.data, axis=1)
                return SingleData(tmp_data, self.index)
            else:
                raise ValueError(f"axis must be None, 0 or 1")
    ```
  - 效果：当数据为空/全 `nan` 时，`mean` 仍返回 `nan`，但不会再在回测日志里刷 RuntimeWarning。

---

后续如有新的问题和解答，可以在本文件继续追加新的章节（按时间或按主题分类均可）。

## 4. 为什么 `tuner` 里会调用 `estimator` 命令，但我机器上没有？

- **原因**：这是 Qlib 早期/历史用法的设计——`tuner` 会通过子进程调用一个名为 `estimator` 的命令行入口点（`console_scripts`）。
- **为什么你没有**：如果你是“源码直接跑”（没有 `pip install` 安装成包），或者安装时没有把对应入口点装进当前环境的 `bin/`，就不会生成 `estimator` 这个可执行命令。
- **结论**：不是 Python 里缺了一个叫 `estimator` 的第三方库；它更像是“安装后才会出现的一个命令”。

## 5. 在源码运行环境下如何使用 `tuner`？

本仓库里把 `tuner` 调用方式改成了“显式使用当前 Python 解释器运行模块”：

- 估计器（原 `estimator`）现在用：
  - `python -m qlib.contrib.model.launcher -c <estimator_config.yaml>`
- tuner 入口现在可用：
  - `python -m qlib.contrib.tuner.launcher -c <tuner_config.yaml>`

`tuner` 每次采样一组参数后，会启动一个子进程跑一次 qrun workflow，然后从回测产物里读到指标（例如最大回撤），再交给 `hyperopt` 做下一轮搜索。

## 6. 用 `tuner` 优化“最大回撤（max_drawdown）”时怎么配置？

关键点是 `optimization_criteria` 这三项：

```yaml
optimization_criteria:
  report_type: excess_return_with_cost
  report_factor: max_drawdown
  optim_type: max
```

- `report_type`：从 `port_analysis_1day.pkl` 的哪一块结果里取指标；通常用 `excess_return_with_cost` 更贴近真实交易（含手续费）。
- `report_factor`：要优化的指标，这里选 `max_drawdown`。
- `optim_type`：这里用 `max` 是因为 Qlib 里的最大回撤通常是**负数**（例如 `-0.12`），越接近 `0` 越好；“最大化”会把它推向 `-0.05` 这种更小回撤。

workflow 走你已有的 LightGBM 配置，例如：

- `examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_2020_2025.yaml`

如果你想优化 **IC**（`SigAnaRecord` 打印的 `IC`），可以用：

```yaml
optimization_criteria:
  report_type: model
  report_factor: model_pearsonr
  optim_type: max
```

一个“最小可跑”的 `tuner_config.yaml` 示例（你可以先把 `max_evals` 设小一点做冒烟测试）：

```yaml
experiment:
  name: tuner_alpha158_maxdd
  dir: ./.tuner_runs

optimization_criteria:
  report_type: excess_return_with_cost
  report_factor: max_drawdown
  optim_type: max

tuner_pipeline:
  - workflow_config: benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_2020_2025.yaml
    model:
      space: LGBModelSpace
    strategy:
      space: TopkDropoutStrategySpace
    trainer: {}
    max_evals: 5

time_period: {}
data: {}
backtest: {}
qlib_client: {}
```

运行方式：

- `python -m qlib.contrib.tuner.launcher -c <tuner_config.yaml>`

## 7. tuner 跑完之后，最优参数保存在哪里？

以配置里的 `experiment.dir`/`experiment.name` 为根目录（例如 `/qlib/.tuner_smoke/tuner_smoke/`），会生成：

- `global_best_params.json`：整个 `tuner_pipeline` 的全局最优参数
- `estimator_experiment/estimator_experiment_<idx>/local_best_params.json`：某个 pipeline 阶段（第 `<idx>` 个 tuner）内部的最优参数
- `estimator_experiment/estimator_experiment_<idx>/sacred/<run_id>/analysis.pkl`：每次 trial 对应的回测分析结果（tuner 用它来取 `max_drawdown` 等指标）

把 `global_best_params.json` 里的内容应用回 workflow 的方式：

- `model_space` → 覆盖 `task.model.kwargs`
- `strategy_space` → 覆盖 `port_analysis_config.strategy.kwargs`

运行结束时，控制台也会输出两行总结日志，方便你直接看到“最优指标值是多少、对应哪个 run”：

- `BEST RESULT: <report_type>.<report_factor>=<value>, run_id=<...>`
- `GLOBAL BEST RESULT: <report_type>.<report_factor>=<value>, run_id=<...>`

现在也会直接打印最优实验/记录 ID：

- `BEST RUN: experiment_id=<...>, recorder_id=<...>`
- `GLOBAL BEST RUN: experiment_id=<...>, recorder_id=<...>`

如果需要“最优结果对应的实验 ID 和 recorder ID”，可以这样查：

- `estimator_experiment_0/exp_info.json` 里的 `id` 就是 recorder/run id
- `estimator_experiment_0/mlruns/<experiment_id>/meta.yaml` 里的 `experiment_id` 是实验 ID

本次运行记录（供参考，后续每次跑会变化）：

- experiment_id: `1`
- recorder_id: `f4d5ca1eb1b844cbb3dc813ffd90818e`

## 8. Hyperopt 是什么？和 Qlib tuner 的关系是什么？

- `hyperopt` 是一个做超参数搜索的库（Qlib tuner 底层用它来“决定下一组参数该试什么”）。
- 你可以把它理解成一个“自动试参的调度器”：每次试参都会启动一次训练+回测，得到指标后再更新搜索方向。

## 9. 实盘如何预留一部分资金不参与交易？

- 仅在实盘脚本 `examples/live_daily_predict.py` 生效，回测不受影响。
- 在 `trading` 配置里加 `reserve_cash`，会先从实际现金中扣除，再用于预算/下单：
  - `effective_cash = max(actual_cash - reserve_cash, 0)`
- 现在默认值为 `199800000`（即“2 亿只用 20 万”的场景），如需调整，修改该配置即可。

## 10. 实盘日循环如何判断交易日？

- 不再使用本地 `calendars/day.txt` 判断交易日，改为等待 iQuant 写入开盘信号。
- iQuant 会在当天第一次实时 bar 时写入 `state.json`：
  - `phase=market_open`
  - `version=YYYYMMDD`
- Qlib 的循环模式只在检测到 `market_open` 后才开始握手流程，并使用该 `version` 作为本次运行标识。

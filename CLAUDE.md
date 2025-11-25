# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Qlib 是一个面向 AI 的量化投资平台，提供完整的机器学习流水线：数据处理、模型训练、回测，涵盖从 alpha 挖掘到订单执行的整个量化投资链条。支持监督学习、市场动态建模和强化学习等多种范式。

## 安装与设置

### 开发环境安装
```bash
# 首先安装依赖
pip install numpy
pip install --upgrade cython

# 以可编辑模式安装，包含开发依赖
pip install -e .[dev]

# 安装 pre-commit hooks（提交时自动格式化代码）
pre-commit install
```

### 编译 Cython 扩展
Qlib 需要编译 Cython 模块（`rolling` 和 `expanding`）：
```bash
make prerequisite  # 从 .pyx 文件编译生成必要的 .so 文件
```

### 安装可选依赖
```bash
make rl           # 强化学习功能
make analysis     # 分析和绘图工具
make test         # 测试依赖
```

## 数据准备

Qlib 使用自定义的 `.bin` 格式进行高效的数据存储和检索。运行示例前需要准备数据：

```bash
# 下载中国市场数据（日频）
python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/cn_data --region cn

# 下载 1 分钟频率数据
python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/cn_data_1min --region cn --interval 1min

# 下载美国市场数据
python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/us_data --region us
```

**注意**：官方数据集暂时不可用，可使用社区贡献的数据：
```bash
wget https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz
mkdir -p ~/.qlib/qlib_data/cn_data
tar -zxvf qlib_bin.tar.gz -C ~/.qlib/qlib_data/cn_data --strip-components=1
```

## 运行测试

```bash
# 运行所有测试
pytest tests/

# 运行测试但排除慢速测试
pytest tests/ -m "not slow"

# 运行特定测试文件
pytest tests/test_all_pipeline.py
```

## 代码架构

### 核心模块

**qlib/data/** - 数据层，高性能数据基础设施
- `_libs/`: Cython 优化的滚动/扩展窗口操作
- `dataset/`: 数据集抽象（handler, loader, processor）
- `ops.py`: 表达式引擎算子，用于构建公式化 alpha
- `cache.py`: 内存和磁盘缓存系统

**qlib/model/** - 模型层
- `base.py`: 基础模型类
- `trainer.py`: 模型训练编排
- `meta/`: 元学习框架（概念漂移适应）
- `ens/`: 集成模型实现

**qlib/workflow/** - 实验管理
- `exp.py`, `expm.py`: 实验类
- `recorder.py`: 基于 MLflow 的实验追踪
- `task/`: 分布式训练任务管理
- `online/`: 在线服务和模型滚动

**qlib/backtest/** - 回测引擎
- `executor.py`: 交易执行模拟
- `exchange.py`: 交易所模拟（包含成本建模）
- `account.py`, `position.py`: 投资组合会计

**qlib/strategy/** - 交易策略
- `base.py`: 策略基类
- 具体实现位于 `qlib/contrib/strategy/`

**qlib/contrib/** - 贡献的模型、策略和数据处理器
- `model/`: 预构建模型（LightGBM, LSTM, GRU, Transformer 等）
- `data/handler.py`: Alpha158, Alpha360 数据集处理器
- `strategy/`: TopkDropoutStrategy 等交易策略

**qlib/rl/** - 强化学习框架
- 订单执行优化
- 投资组合管理智能体

### 数据流

1. **数据准备**: 原始数据 → `.bin` 格式（通过 `scripts/data_collector/` 或 `scripts/get_data.py`）
2. **数据加载**: `DataLoader` 加载 `.bin` 文件，通过表达式引擎计算特征
3. **数据处理**: `DataHandler` 应用处理器（归一化、过滤等）
4. **数据集创建**: `Dataset` 准备训练/验证/测试集
5. **模型训练**: 模型消费数据集，由 `Recorder` 追踪
6. **策略**: 策略使用模型预测生成交易信号
7. **回测**: `Executor` 模拟交易，`Exchange` 处理订单匹配

## 常用命令

### 使用 qrun 运行工作流
```bash
cd examples  # 避免在包含 'qlib' 包的目录下运行
qrun benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml
```

### 调试工作流
```bash
python -m pdb qlib/cli/run.py examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml
```

### 运行特定模型基准测试
```bash
python examples/run_all_model.py run --models=lightgbm
```

### 代码质量检查
```bash
# 使用 black 格式化代码
python -m black . -l 120

# 使用 flake8 检查
flake8 --ignore=E501,F541,E266,E402,W503,E731,E203 --per-file-ignores="__init__.py:F401,F403" qlib

# 使用 pylint 检查（完整命令见 Makefile）
make pylint

# 运行所有 lint 检查
make lint
```

### 构建文档
```bash
cd docs/
make html  # 输出到 public/ 或 $READTHEDOCS_OUTPUT/html
```

### 清理构建产物
```bash
make clean      # 删除构建产物、.pyc 文件、缓存
make deepclean  # 同时删除虚拟环境和 pre-commit hooks
```

## 工作流配置（YAML）

Qlib 工作流通过 YAML 配置文件定义，包含以下关键部分：

- **qlib_init**: 数据路径和区域设置
- **task**: 定义模型、数据集和记录
  - `model`: 模型类和超参数
  - `dataset`: DatasetH，包含 handler（如 Alpha158）和 segments（train/valid/test）
  - `record`: 实验追踪（SignalRecord, SigAnaRecord, PortAnaRecord）
- **port_analysis_config**: 回测策略和交易所参数

配置示例位于 `examples/benchmarks/[MODEL]/workflow_config_*.yaml`

## 关键设计模式

### Handler-Processor 模式
数据处理器使用处理器流水线转换原始数据：
```python
# 在数据集配置中
handler:
    class: Alpha158
    kwargs:
        start_time: "2008-01-01"
        processors:  # 可选的处理步骤
            - class: DropnaProcessor
            - class: CSZScoreNorm
```

### 表达式引擎
Qlib 的特征工程表达式语言：
- 基础算子: `$close`, `$open`, `$high`, `$low`, `$volume`
- 时间算子: `Ref($close, 1)`, `Mean($close, 5)`, `Std($close, 20)`
- 截面算子: `Rank($close)`, `CSRank($close)`
- 复杂表达式: `($close - Ref($close, 1)) / Ref($close, 1)`

实现位于 `qlib/data/ops.py`

### 模块化任务定义
模型、数据集和策略以声明式方式定义：
```yaml
task:
    model:
        class: LGBModel
        module_path: qlib.contrib.model.gbdt
```
允许通过 `qlib.init` + `task_train` 运行完整工作流。

### Recorder 集成
所有实验通过兼容 MLflow 的 recorder 追踪：
- 默认位置: 工作目录下的 `mlruns/`
- 记录内容: 模型文件、预测结果、回测结果、分析图表
- 通过 `qlib.workflow.recorder.Recorder` 访问

## Python 版本支持

- 支持 Python 3.8, 3.9, 3.10, 3.11, 3.12
- 需要 Pandas >= 1.1（fillna/ffill/bfill 兼容性）
- 部分模型（如 TFT）需要特定版本（tensorflow==1.15.0 需要 Python 3.6-3.7）

## 重要注意事项

- 始终从 `examples/` 目录运行示例以避免模块冲突
- Qlib 将调整后价格归一化为每只股票首个交易日的 1.0
- 使用 `$factor` 将调整后价格转换为原始价格：`$close / $factor`
- 贡献模型时，将其放在 `examples/benchmarks/[MODEL_NAME]/` 目录
- 修改 `.pyx` 文件后必须重新编译 Cython 模块：`make prerequisite`
- 项目使用 pandas 2.x+，`group_keys` 默认值已更改，部分旧脚本可能存在问题
- 就是claude code你自己新建的测试脚本，放在test_claude_code的文件夹下吧，便于管理
- 我这个项目用了anaconda的虚拟环境，虚拟环境的名字是qlib，注意执行的时候使用这个

## 文档更新规范（重要）

**每次实现新功能后必须更新文档！**

### 文档更新要求

1. **功能文档**: `docs/self/features.md`
   - 这个文档用于记录项目中新增和改进的功能
   - 每次实现新功能并经过测试验证后，必须立即更新此文档
   - 确保代码更新和文档保持同步

2. **更新内容包括**:
   - 功能概述和实现时间
   - 技术架构和设计方案
   - 核心代码文件列表
   - 使用流程和示例
   - 已修复的问题（如果适用）
   - 相关的 git 提交信息

3. **更新时机**:
   - 功能实现完成 → 测试验证通过 → **立即更新文档**
   - 不要等到最后才更新，避免遗忘细节

4. **文档格式**:
   - 使用 Markdown 格式
   - 添加清晰的标题和目录
   - 使用代码块展示关键代码
   - 添加流程图和表格辅助说明
   - 标注功能状态（✅ 已完成、🚧 进行中、⚠️ 已知问题）

### 示例

参考 `docs/self/features.md` 中 "iQuant 实盘交易集成" 章节的格式，包含：
- 功能概述
- 技术架构（流程图 + 状态机表格）
- 已实现功能（分模块列出）
- 核心文件列表
- 使用流程
- 已修复问题

## qrun 工作流处理详细流程

以 `examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_2020_2025.yaml` 为例，`qrun` 的处理流程如下：

### 1. **入口点** (`qlib/cli/run.py`)
```bash
qrun examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_2020_2025.yaml
```

### 2. **配置文件解析**
- 使用 Jinja2 模板引擎渲染 YAML（支持环境变量替换）
- 使用 YAML 安全解析器加载配置
- 支持配置继承机制（BASE_CONFIG_PATH）

### 3. **系统配置**
- 处理 `sys` 部分，添加 Python 路径
- 支持绝对路径和相对路径

### 4. **Qlib 初始化**
根据配置中的 `qlib_init` 部分初始化：
```yaml
qlib_init:
    provider_uri: "~/.qlib/qlib_data/cn_data"
    region: cn
```

### 5. **任务训练核心流程** (`qlib/model/trainer.py:task_train`)

#### 5.1 **开始实验记录**
- 创建 MLflow recorder 记录实验过程
- 保存任务配置参数

#### 5.2 **模型和数据集初始化**
根据配置中的 task 部分：
```yaml
model:
    class: LGBModel
    module_path: qlib.contrib.model.gbdt
dataset:
    class: DatasetH
    handler:
        class: Alpha158
        module_path: qlib.contrib.data.handler
    segments:
        train: [2020-01-01, 2022-12-31]
        valid: [2023-01-01, 2024-12-31]
        test: [2025-01-01, 2025-08-01]
```

#### 5.3 **模型训练**
- 调用 `model.fit(dataset)` 进行训练
- 使用 LightGBM 在 Alpha158 特征上训练
- 训练期：2020-2022，验证期：2023-2024

#### 5.4 **记录生成**
处理 record 配置：
```yaml
record:
    - class: SignalRecord      # 保存模型预测
    - class: SigAnaRecord      # 信号分析
    - class: PortAnaRecord     # 投资组合分析
```

### 6. **投资组合回测**
基于 `port_analysis_config`：
- 使用 TopkDropoutStrategy（选择前10只股票，每期丢弃2只）
- 回测期：2025-01-01 到 2025-08-01
- 初始资金：10000

### 7. **输出结果**
- 模型保存在 `mlruns/` 目录
- 包含预测结果、回测表现、分析图表等

### 关键代码位置
- 主入口：`qlib/cli/run.py:workflow()`
- 任务训练：`qlib/model/trainer.py:task_train()`
- 任务执行：`qlib/model/trainer.py:_exe_task()`

这个流程从配置解析到模型训练、再到回测分析，形成了一个完整的量化研究工作流。

## TopkDropoutStrategy 笔记

- 位置：`qlib/contrib/strategy/signal_strategy.py:75`，继承 `BaseSignalStrategy`，核心方法 `generate_trade_decision`。
- 参数：`topk` 目标持仓数；`n_drop` 每期替换数；`method_sell`（bottom/random）；`method_buy`（top/random）；`hold_thresh` 最短持有天数（基于 `get_stock_count`）；`only_tradable` 是否仅对可交易标的决策；`forbid_all_trade_at_limit` 涨跌停时是否完全禁止交易。
- 交易逻辑：按交易步获取预测窗口信号，若 DataFrame 仅取首列；`only_tradable` 控制买卖候选筛选函数。当前持仓得分排序为 `last`；买入候选 `today`（top 取未持仓高分，random 在前 topk 内随机），合并后根据 `method_sell` 选择卖出列表；卖出前检查可交易与持有天数，成交后更新现金；买入预算 `cash * risk_degree / len(buy)`，按成交价与交易单位取整生成 BUY 订单；最终返回 `TradeDecisionWO`（卖单+买单）。
- 函数作用/输入：`generate_trade_decision` 在回测每个交易步依据预测信号分数和当前仓位生成本期的买卖订单，返回 `TradeDecisionWO`。入参 `execute_result=None`（占位，上一步执行结果未用）；依赖内部状态：`trade_calendar`（窗口时间/频率）、`signal`（`pred_score`）、`trade_position`（持仓/现金）、`trade_exchange`（可交易/成交价/涨跌停/交易单位），以及策略参数（`topk`、`n_drop`、`method_buy`、`method_sell`、`hold_thresh`、`only_tradable`、`forbid_all_trade_at_limit`、`risk_degree`）。
- 交易日历：`TradeCalendarManager`（`qlib/backtest/utils.py:40-110`）按 `time_per_step` 和 `start_time/end_time` 计算 `trade_len`，初始化 `trade_step=0`；每步执行器推进 `trade_step += 1`。日频（`time_per_step="day"`）时，`trade_step` 取值为 0...`trade_len-1`，对应交易区间内的每个交易日。
- Position 说明：`Position`（`qlib/backtest/position.py`）管理账户现金与各股票持仓量，提供 `get_cash()`、`get_stock_list()`、`get_stock_amount(code)`、`get_stock_count(code, bar=...)` 等查询。撮合时传入的 `position` 会被更新数量与现金。`TopkDropoutStrategy` 中用 `copy.deepcopy(self.trade_position)` 得到临时 `current_temp`，卖出成交更新现金/数量时不污染原始持仓。
- 使用示例：`examples/online_srv/online_management_simulate.py:103-110` 将 Rolling 生成的信号转成 `score` 列，配置 `{"topk": 30, "n_drop": 3, "signal": signals.to_frame("score")}` 构建策略，传入 `backtest_daily` 后用 `risk_analysis` 评估。
- 调用链：`_exe_task`（`qlib/model/trainer.py:41-70`）遍历 record，用 `init_instance_by_config` 生成 `PortAnaRecord`；`PortAnaRecord._generate`（`qlib/workflow/record_temp.py:465-544`）在 `normal_backtest` 中传入 `strategy_config`；`qlib/backtest/__init__.py:get_strategy_executor` 用 `init_instance_by_config` 实例化 `TopkDropoutStrategy`；`qlib/backtest/backtest.py:26-70` 的 `backtest_loop/collect_data_loop` 每个时间步调用 `generate_trade_decision`。
- `deal_order`：`qlib/backtest/exchange.py:421-468`，检查可交易性后计算成交价/金额/成本，更新 `trade_account` 或 `position`（二选一），返回 `(trade_val, trade_cost, trade_price)`；会修改传入的 `Order` 的成交字段。
- 参数配置：`hold_thresh` 在策略 kwargs 中指定（默认 1，定义见 `TopkDropoutStrategy.__init__`），控制持有不足阈值则跳过卖出；通过 YAML/配置传入 `strategy.kwargs.hold_thresh`。
- 实盘 TODO（需替换/扩展内置回测逻辑）：
  - iQuant ↔ qlib 实盘同步（两阶段，Topk 不改，T-1 特征 + 当日 quotes 执行，阻塞握手）：
    - 角色分工：
      - iQuant：产出并写入 CSV（持仓/行情），根据 state.json 阻塞等待/推进；最终实盘下单并记日志。
      - qlib：模型推理/Topk 选股、生成候选、读取 quotes_live 计算份额，输出执行清单；更新 state.json。
    - 阶段/状态（state.json 持有 phase、version、timestamp；文件写临时名后 rename，UTF-8(-sig)，列名小写无空格）：
      - P1 iQuant → positions_ready：写 positions_live.csv（account/pos/available/cost/last_price，带 ts/version），state=positions_ready:v。
      - P2 qlib Phase1 → symbols_ready：用 T-1（或数据最后交易日）跑模型/Topk 得候选/权重，不算份额，输出 symbols_req.csv（code、direction/score…，带 version），state=symbols_ready:v。
      - P3 iQuant → quotes_ready：仅对 symbols_req 标的抓当日 quotes_live（last/bid1/ask1/high_limit/low_limit/ts），state=quotes_ready:v。
      - P4 qlib Phase2 → orders_ready：用持仓+quotes_live 计算 shares/price，生成 orders_to_exec.csv（order_id/action/shares/price/valid_until/version）。价格规则：买价 ask1→last→high_limit，卖价 bid1→last→low_limit，整手取整；缺当日行情则跳过/报错，不用昨收。state=orders_ready:v。
      - P5 iQuant 执行：仅在 orders_ready 且版本匹配时实盘下单（实时 bar，order_id 幂等），可回写 orders_log.csv、state=exec_done/exec_failed。超时/旧版/过期信号跳过并记日志。
    - qlib 侧实现建议：
      - 新增 LivePipeline（两阶段入口 + state/CSV IO + 阻塞等待）：Phase1 跑推理/Topk → symbols_req；Phase2 读 quotes_live → 用当日价算份额 → orders_to_exec。
      - 可选 LiveExchange（继承 Exchange）：get_deal_price 改用 quotes_live（买 ask1→last→high_limit，卖 bid1→last→low_limit），放宽日历检查；或在 Phase2 直接用 quotes_live 自算 shares/price，不调用 generate_trade_decision 的价格部分。
    - iQuant 侧要点：
      - 只在对应 state 达成时推进（positions_ready → symbols_ready → quotes_ready → orders_ready）。
      - 报价必须是当日实时（带 ts），缺行情可选择跳过该票或整个批次；下单市价 prType=4 price=-1（或限价按订单文件给定）。
      - order_id 唯一（含日期/序号），执行幂等；日志/错误输出明确版本与原因。
  - 重写/继承 `Exchange` 对接实时行情与交易前置，`deal_order` 返回真实成交价/成本，支持部分成交/撤单；订单需区分限价/市价、滑点、最小交易单位、涨跌停校验。
  - 费用与规则：补实盘手续费/印花税/过户费/融券成本，并处理 T+1 等市场规则（当前 `hold_thresh` 只是步数限制）。
  - 时序对齐：保证信号产出与下单时间匹配（`shift=1` 行为），日/周频需用对应日历与行情。
  - tradable 逻辑：基于实时涨跌停/停牌状态判断可交易性，不能依赖历史行情标记。
  - 仓位同步：启动时从券商同步真实现金/持仓，持续用成交回报更新，不使用默认内账本。
  - 随机性与日志：random 模式建议改为确定性或固定 seed；移除/改为日志的调试 print（如 trade_step==0 打印）。
- 交易日历：`TradeCalendarManager`（`qlib/backtest/utils.py:40-110`）按 `time_per_step` 和 `start_time/end_time` 计算 `trade_len`，初始化 `trade_step=0`；每步执行器推进 `trade_step += 1`。日频（`time_per_step="day"`）时，`trade_step` 取值为 0...`trade_len-1`，对应交易区间内的每个交易日。
## 实盘 TODO（给 Claude 的指引）

### 当前已实现
- `examples/live_daily_predict.py`：运行后依次写 `state=positions_needed → positions_ready → symbols_ready → quotes_ready → orders_ready`。Phase1 用 T-1 数据选股，Phase2 读取 iQuant 输送的 quotes_live 生成 `orders_to_exec.csv`。
- `examples/iquant_qlib.py`（GBK）：同一脚本响应三阶段，并且所有操作仅在 `ContextInfo.is_last_bar()` 为真时执行。
  - `positions_needed`：调用 `get_trade_detail_data(accountID, ACCOUNT_TYPE, "POSITION", strategyName)` 导出 `positions_live.csv`，之后写 `positions_ready`。
  - `symbols_ready`：读取 `symbols_req.csv`，优先用 `ContextInfo.get_full_tick(stock_list)` 导出 `quotes_live.csv`（失败时再尝试旧 API）。
  - `orders_ready`：读取 `orders_to_exec.csv`，支持 DRY_RUN/整手/市价或限价，下单后写 `exec_done/exec_failed`。
- `qlib/backtest/live_exchange.py`：`LiveExchange` 优先使用 quotes_live 的 bid1/ask1/last (含涨跌停保护)。

完成上述 TODO 后，请在此文档更新“已实现/待完成”列表，以便后续 review。
## 实盘 Debug 说明（交接给 Claude）

当前已经实现的逻辑：
- `examples/live_daily_predict.py` 启动后依次写 `state=positions_needed → positions_ready → symbols_ready → quotes_ready → orders_ready`。Phase1 读取 T-1 数据选股，Phase2 读取 iQuant 写入的 `quotes_live.csv` 生成 `orders_to_exec.csv`。
- `examples/iquant_qlib.py`（GBK）常驻在 iQuant 客户端：
  - 当 `state.phase = positions_needed` 时调用 `get_trade_detail_data(accountID, ACCOUNT_TYPE, "POSITION", strategyName)` 导出 `positions_live.csv`，写回 `positions_ready`。
  - 当 `state.phase = symbols_ready` 时读取 `symbols_req.csv`，调用 `ContextInfo.get_full_tick(stock_list)`（失败则尝试其它 API）导出 `quotes_live.csv`，写回 `quotes_ready`。
  - 当 `state.phase = orders_ready` 且 `ContextInfo.is_last_bar()` 为 True 时读取 `orders_to_exec.csv` 下单，支持 DRY_RUN/整手/市价或限价，最后写 `exec_done/exec_failed`。
- `qlib/backtest/live_exchange.py` 覆盖 `get_deal_price`，优先使用 quotes_live 的 bid1/ask1/last（含涨跌停保护）。

当前需要你（Claude）调试的问题：
1. 在实际环境中，运行 `live_daily_predict.py` 时要确保 `state.json` 先写 `positions_needed`，等待 iQuant 导出持仓后再进入 Phase1。请确认脚本在等待阶段没有因为缺少 `positions_live.csv` 抛错。
2. iQuant 侧脚本在 `positions_needed` / `symbols_ready` 阶段尝试自动导出 CSV。如果 `get_trade_detail_data` 或 `get_full_tick` 返回为空，请补充日志和 fallback（例如提示手动生成文件），并确保 state 不会误写成 ready。
3. 在 `quotes_ready` → `orders_ready` 之间，确认 `quotes_live.csv` 的列名（last/bid1/ask1/涨跌停）与 qlib Phase2 读取逻辑一致，避免因列缺失导致下单份额计算失败。
4. 全程使用默认配置（无需外部 config），运行顺序：
   - 启动 iQuant 脚本（GBK）
   - 在 qlib 虚拟环境中 `python examples/live_daily_predict.py`（确保 `pip install pandas`、`import qlib` 正常）
   - 观察 `state.json` 随阶段变化；若某阶段阻塞，请调试对应导出逻辑。

请不要增加新的功能，只需确保上述流程跑通，定位/修复在等待持仓或导出行情时的异常。调试完成后，将修改和关键日志写入本文件的"实盘 Debug 说明"段落，便于 review。
- 涉及到iquant api接口的，参考file:///D:/%E5%9B%BD%E4%BF%A1iQuant%E7%AD%96%E7%95%A5%E4%BA%A4%E6%98%93%E5%B9%B3%E5%8F%B0/HTML/guosenPythonApiHelp/iQuant_Python_API_Doc.html#id24

---

## 已知问题与修复记录

### Bug #1: iQuant 实盘下单失败 - passorder 返回 0（已修复）

**发现时间**: 2025-11-20
**修复提交**: `43ef5eeb` - fix: 修复 iQuant 实盘下单失败问题

#### 问题描述

在交易时间内调用 `passorder` 下单时：
- `passorder` 始终返回 `0`（表示失败）
- 订单完全未进入券商系统
- iQuant 客户端的【委托】列表中看不到任何订单记录
- 没有任何拒绝提示或错误弹窗
- 订单回调函数（`orderError_callback`、`order_callback`）完全没有触发

#### 症状特征

```python
[DEBUG] passorder 返回值: type=<class 'int'>, value=0
[iQuant] 已提交 300122.SZ 买入 2200 股 (order_id=xxx, ver=xxx)
```

看似"已提交"，但实际上：
- ✅ 运行模式已确认为"实盘模式"
- ✅ 账户 ID 设置正确
- ✅ 当前时间在交易时间内（9:30-11:30 或 13:00-15:00）
- ✅ 股票代码格式正确（`300122.SZ`）
- ✅ passorder 参数与成功案例完全一致
- ❌ 但订单就是不进入系统

#### 根本原因

**缺少 `is_last_bar()` 检查**，导致在历史回放/预热阶段就执行了下单逻辑。

iQuant 的 `handlebar()` 函数在策略运行时会先回放历史数据（预热阶段），然后才进入实时 bar。在历史回放阶段执行的 `passorder` 调用会被 iQuant 内部忽略，不会提交到券商系统，也不会触发任何回调或错误提示。

对比成功案例 `iquant_lizi.py`，发现关键差异：

```python
# ✅ 成功案例 (iquant_lizi.py)
def handlebar(ContextInfo):
    # 检查是否为实时 bar
    is_last = getattr(ContextInfo, 'is_last_bar', lambda: True)
    if not is_last():
        return  # 非实时 bar，跳过执行

    # 只在实时 bar 执行下单逻辑
    ...

# ❌ 问题版本 (iquant_qlib.py - 修复前)
def handlebar(ContextInfo):
    # 直接开始轮询和下单，没有检查 is_last_bar()
    if getattr(ContextInfo, "_polling_started", False):
        return
    ...
```

#### 其他发现的问题

1. **`ContextInfo.set_account()` 调用不必要**
   - 成功案例只设置 `ContextInfo.accid = ACCOUNT_ID`
   - 不需要额外调用 `set_account()` 方法

2. **缺少诊断日志**
   - 无法确认是否在正确的 bar 上执行
   - 无法验证账户设置是否生效

#### 修复方案

1. **添加 `is_last_bar()` 检查**（关键修复）
```python
def handlebar(ContextInfo):
    # 获取 is_last_bar 函数
    is_last_bar_func = getattr(ContextInfo, 'is_last_bar', lambda: True)
    is_last = is_last_bar_func()

    # 只在实时 bar 执行
    if not is_last:
        print(f"[DEBUG] 非实时 bar，跳过执行")
        return
```

2. **移除 `set_account()` 调用**
```python
def init(ContextInfo):
    if ACCOUNT_ID:
        ContextInfo.accid = ACCOUNT_ID
        # ❌ 移除这行: ContextInfo.set_account(ACCOUNT_ID)
```

3. **增强诊断日志**
```python
# handlebar 入口诊断
print(f"[DEBUG][handlebar] barpos={ContextInfo.barpos}, "
      f"is_last_bar={is_last}, accid={ContextInfo.accid}")

# 下单前账户验证
test_positions = get_trade_detail_data(acc_id, ACCOUNT_TYPE, "position")
print(f"[DEBUG] 账户验证成功，当前持仓 {len(test_positions)} 条")

# passorder 详细诊断
print(f"[DEBUG][passorder] 准备调用 passorder")
print(f"[DEBUG][passorder] 参数: code={code}, qty={qty}, accid={acc_id}")
ret = passorder(...)
print(f"[DEBUG][passorder] 返回值: {ret} ({'成功' if ret > 0 else '失败'})")
```

#### 验证结果

修复后在交易时间内测试：
- ✅ `is_last_bar=True` 确认在实时 bar 执行
- ✅ passorder 返回值 > 0（成功）
- ✅ 订单成功进入券商系统
- ✅ iQuant 客户端【委托】列表中可见订单
- ✅ 订单正常成交

#### 经验教训

1. **iQuant 策略生命周期**
   - `handlebar()` 会先执行历史回放，再执行实时 bar
   - **必须使用 `is_last_bar()` 区分**历史数据和实时数据
   - 在历史 bar 上的交易操作会被静默忽略

2. **passorder 返回值含义**
   - 返回 `0`：下单失败，订单未进入系统
   - 返回 `> 0`：下单成功，返回值通常是委托号
   - **但在非实时 bar 上调用会返回 0，且不会有任何错误提示**

3. **调试实盘问题的要点**
   - 对比成功案例的完整流程，不只是 API 调用
   - 检查执行时机（历史 vs 实时）
   - 添加详细的上下文诊断日志
   - 验证环境状态（账户、时间、bar 类型等）

4. **iQuant 回调函数触发条件**
   - 回调函数只在实盘模式 + 实时 bar 上触发
   - 历史回放阶段不会触发任何回调
   - 如果回调不触发，首先检查 `is_last_bar()`

#### 相关文件

- `examples/iquant_qlib.py` - 已修复的实盘下单脚本
- `examples/iquant_lizi.py` - 成功案例（参考实现）
- `examples/live_daily_predict.py` - qlib 侧实盘预测脚本

#### 相关提交

- `43ef5eeb` - fix: 修复 iQuant 实盘下单失败问题

---

### Feature #1: 实盘账户现金自动获取（已实现）

**实现时间**: 2025-11-20
**功能**: 从 iQuant 自动获取账户可用资金，替代配置文件中的固定值

#### 背景问题

在之前的实现中存在数据来源不一致的问题：
- ✅ **持仓数据**：使用从 iQuant 读取的实际持仓
- ❌ **账户现金**：使用配置文件的固定值（`total_cash=50000`）

这导致：
1. 无法反映真实账户余额
2. 可能过度下单或资金使用不足
3. 配置值与实际值冲突时，难以判断使用哪个

#### 解决方案

**原则**：配置值与实际数据冲突时，**优先使用从 iQuant 读取的实际数据**

**实现：**

##### 1. iQuant 侧 (`examples/iquant_qlib.py`)

新增 `_fetch_account_cash()` 函数：
```python
def _fetch_account_cash(ContextInfo):
    """获取账户可用资金"""
    acc_id = ACCOUNT_ID or getattr(ContextInfo, "accid", None)

    # 调用 iQuant API 获取账户信息
    data = get_trade_detail_data(acc_id, ACCOUNT_TYPE, "account")

    # 提取 m_dAvailable 字段（可用资金）
    account_obj = data[0] if isinstance(data, (list, tuple)) else data
    if hasattr(account_obj, 'm_dAvailable'):
        cash = getattr(account_obj, 'm_dAvailable', None)
        print(f"[INFO] ✅ 从 iQuant 获取账户现金: {cash:.2f} 元")
        return float(cash)
    return None
```

修改 `_convert_positions()` 添加 CASH 行：
```python
def _convert_positions(raw, cash=None):
    # ... 处理持仓数据 ...

    # 添加特殊的 CASH 行
    if cash is not None:
        cash_row = pd.DataFrame([{
            "code": "CASH",
            "position": float(cash),
            "available": float(cash),
            "cost_price": "",
            "last_price": "",
        }])
        df = pd.concat([df, cash_row], ignore_index=True)
    return df
```

导出的 `positions_live.csv` 格式：
```csv
code,position,available,cost_price,last_price
SH600000,1000,1000,10.5,11.2
SZ000001,500,500,15.3,16.1
CASH,50000.00,50000.00,,
```

##### 2. qlib 侧 (`examples/live_daily_predict.py`)

修改 `_read_positions()` 识别 CASH 行：
```python
def _read_positions(path):
    # ... 读取 CSV ...

    holdings = {}
    cash = None

    for _, row in df.iterrows():
        code_raw = str(row[code_col]).strip().upper()

        # 检查是否是 CASH 行
        if code_raw == "CASH":
            cash = float(row[pos_col])
            print(f"[live] ✅ 从 positions_live.csv 读取账户现金: {cash:.2f} 元")
            continue

        # ... 处理普通持仓 ...

    return holdings, cash  # 返回元组
```

使用实际现金初始化交易配置：
```python
def main():
    # ... Phase0: 读取持仓 ...
    holdings, cash_from_iquant = _read_positions(positions_path)

    # 优先使用实际现金，否则回退到配置值
    config_cash = trading_raw.get("total_cash", 50000)
    if cash_from_iquant is not None:
        actual_cash = cash_from_iquant
        print(f"[live] ✅ 使用从 iQuant 读取的实际总资金: {actual_cash:.2f} 元")
    else:
        actual_cash = config_cash
        print(f"[live] ⚠️  使用配置文件的默认总资金: {actual_cash:.2f} 元")

    base_trading_cfg = TradingConfig(
        total_cash=actual_cash,  # 使用实际现金
        current_holdings=holdings,
        # ...
    )
```

#### 关键特性

✅ **数据优先级**：实际 iQuant 数据 > 配置默认值
✅ **明确日志**：清楚显示使用的是实际值还是配置值
✅ **向后兼容**：如果 CASH 行不存在，自动回退到配置值
✅ **一致性**：持仓和现金都使用从 iQuant 读取的实际值

#### 预期日志输出

**iQuant 侧：**
```
[DEBUG] _fetch_account_cash: acc_id=410015004039, ACCOUNT_TYPE=STOCK
[DEBUG] calling get_trade_detail_data(acc_id=410015004039, account_type=STOCK, data_type='account')
[DEBUG] 提取账户可用资金: 50000.0
[INFO] ✅ 从 iQuant 获取账户现金: 50000.00 元
[DEBUG] 添加 CASH 行: 可用资金=50000.0
[INFO] 导出持仓 5 条 -> positions_live.csv
```

**qlib 侧：**
```
[live] ✅ 从 positions_live.csv 读取账户现金: 50000.00 元
[live] 读取到持仓: 4 只股票
[live] ✅ 使用从 iQuant 读取的实际总资金: 50000.00 元
```

#### 相关文件

- `examples/iquant_qlib.py` - 新增账户现金获取逻辑 (lines 303-348, 432-459)
- `examples/live_daily_predict.py` - 读取和使用实际现金 (lines 269-287, 633-683)

#### API 参考

- iQuant API: `get_trade_detail_data(accountID, ACCOUNT_TYPE, "account")` - 获取账户信息
- 返回对象字段: `m_dAvailable` - 可用资金（浮点数）
- 详细文档: `D:/国信iQuant策略交易平台/HTML/guosenPythonApiHelp/iQuant_Python_API_Doc.html`
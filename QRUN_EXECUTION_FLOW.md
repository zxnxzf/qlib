# qrun 执行流程详解

## 命令
```bash
cd D:/code/qlib/qlib/examples
qrun benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_2020_2025.yaml
```

---

## 完整执行流程

### 第1步：命令行入口
**文件**: `qlib/cli/run.py` (Line 152-157)
```python
def run():
    fire.Fire(workflow)  # 使用 fire 库解析命令行参数

if __name__ == "__main__":
    run()
```

**作用**:
- qrun 命令映射到 `qlib.cli.run:run()` 函数
- 使用 Google Fire 库自动解析命令行参数
- 调用 `workflow()` 函数

---

### 第2步：解析 YAML 配置
**文件**: `qlib/cli/run.py` (Line 86-108)
```python
def workflow(config_path, experiment_name="workflow", uri_folder="mlruns"):
    # 1. 渲染模板（支持环境变量替换）
    rendered_yaml = render_template(config_path)

    # 2. 解析 YAML
    yaml = YAML(typ="safe", pure=True)
    config = yaml.load(rendered_yaml)
```

**涉及文件**:
- `examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_2020_2025.yaml`

**配置结构**:
```yaml
qlib_init:          # Qlib 初始化配置
task:               # 任务配置
  model:            # 模型配置
  dataset:          # 数据集配置
  record:           # 记录器配置
```

---

### 第3步：初始化 Qlib
**文件**: `qlib/cli/run.py` (Line 138-143)
```python
if "exp_manager" in config.get("qlib_init"):
    qlib.init(**config.get("qlib_init"))
else:
    exp_manager = C["exp_manager"]
    exp_manager["kwargs"]["uri"] = "file:" + str(Path(os.getcwd()).resolve() / uri_folder)
    qlib.init(**config.get("qlib_init"), exp_manager=exp_manager)
```

**调用**: `qlib/__init__.py:init()` 函数

**作用**:
- 设置数据路径: `~/.qlib/qlib_data/cn_data`
- 设置区域: `cn` (中国)
- 初始化 MLflow 实验管理器，指向 `examples/mlruns/`

---

### 第4步：执行训练任务
**文件**: `qlib/cli/run.py` (Line 147)
```python
recorder = task_train(config.get("task"), experiment_name=experiment_name)
```

**调用**: `qlib/model/trainer.py:task_train()`

---

### 第5步：创建模型实例
**根据配置**:
```yaml
task:
  model:
    class: LGBModel
    module_path: qlib.contrib.model.gbdt
```

**涉及文件**: `qlib/contrib/model/gbdt.py`

**模型类**: `LGBModel`
- 基于 LightGBM 的梯度提升树模型
- 继承自 `qlib.model.base.Model`

**超参数**:
```python
{
    'loss': 'mse',
    'colsample_bytree': 0.8879,
    'learning_rate': 0.0421,
    'subsample': 0.8789,
    'lambda_l1': 205.6999,
    'lambda_l2': 580.9768,
    'max_depth': 8,
    'num_leaves': 210,
    'num_threads': 20,
    'num_boost_round': 2000,         # 最大训练轮数
    'early_stopping_rounds': 100,    # 早停轮数
    'verbose_eval': 50               # 日志输出频率
}
```

---

### 第6步：创建数据集
**根据配置**:
```yaml
task:
  dataset:
    class: DatasetH
    module_path: qlib.data.dataset
    kwargs:
      handler:
        class: Alpha158
        module_path: qlib.contrib.data.handler
```

**涉及文件**:
1. `qlib/data/dataset/__init__.py` - `DatasetH` 类
2. `qlib/contrib/data/handler.py` - `Alpha158` 数据处理器

**数据处理流程**:

#### 6.1 Alpha158 特征计算
**文件**: `qlib/contrib/data/handler.py` (Alpha158 类)

**计算 158 个技术指标**:
- 价格类特征: OPEN, HIGH, LOW, CLOSE, VWAP
- 成交量特征: VOLUME
- 移动平均: MA5, MA10, MA20, MA30, MA60
- 波动率: STD5, STD10, STD20, STD30, STD60
- 动量指标: ROC, RSI, MACD
- 横截面排名: RANK, QUANTILE
- ... 共 158 个特征

**底层数据操作**: `qlib/data/ops.py`
- 定义各种技术指标算子
- 时间序列操作: Ref, Mean, Std, Max, Min
- 截面操作: Rank, CSRank, CSZScore

#### 6.2 数据加载
**文件**: `qlib/data/data.py`

**从 .bin 文件加载数据**:
- 数据路径: `~/.qlib/qlib_data/cn_data/`
- 加载股票列表: `instruments/all.txt`
- 加载价格数据: `features/sh600000/close.bin`
- 加载成交量: `features/sh600000/volume.bin`

**时间范围**:
- Handler 时间: 2020-01-01 to 2025-08-01
- 训练集: 2020-01-01 to 2022-12-31 (3年)
- 验证集: 2023-01-01 to 2024-12-31 (2年)
- 测试集: 2025-01-01 to 2025-08-01 (8个月)

#### 6.3 数据处理器
**文件**: `qlib/data/dataset/processor.py`

**默认处理器**:
1. **DropnaLabel**: 删除标签为 NaN 的样本
2. **CSZScoreNorm**: 截面标准化（Z-score）

**在训练集上 fit，然后 transform 所有数据集**

---

### 第7步：模型训练
**文件**: `qlib/contrib/model/gbdt.py:LGBModel.fit()`

**训练过程**:
```python
# 1. 准备数据
df_train, df_valid = dataset.prepare(["train", "valid"])

# 2. 分离特征和标签
x_train, y_train = df_train["feature"], df_train["label"]
x_valid, y_valid = df_valid["feature"], df_valid["label"]

# 3. 创建 LightGBM 数据集
dtrain = lgb.Dataset(x_train, label=y_train)
dvalid = lgb.Dataset(x_valid, label=y_valid, reference=dtrain)

# 4. 训练
self.model = lgb.train(
    params,
    dtrain,
    num_boost_round=2000,
    valid_sets=[dtrain, dvalid],
    valid_names=["train", "valid"],
    early_stopping_rounds=100,
    verbose_eval=50,
)
```

**训练监控**:
- 每 50 轮输出一次日志
- 监控验证集损失 (l2 loss)
- 如果验证集损失连续 100 轮不下降，触发早停

**实际结果**:
- 训练了 310 轮
- 在第 210 轮验证集损失最低: 0.995576
- 触发早停

---

### 第8步：模型预测
**文件**: `qlib/contrib/model/gbdt.py:LGBModel.predict()`

**测试集预测**:
```python
df_test = dataset.prepare("test")
x_test = df_test["feature"]
predictions = self.model.predict(x_test)
```

**输出**: 每只股票在每个交易日的预测评分

---

### 第9步：计算评估指标
**文件**: `qlib/workflow/record_temp.py:SigAnaRecord`

**计算 IC 指标**:
```python
# IC = 预测值与实际收益率的相关系数
ic = pred.corrwith(label, method='pearson')

# ICIR = IC均值 / IC标准差
icir = ic.mean() / ic.std()
```

**实际结果**:
- IC: 0.0697
- ICIR: 0.859
- Rank IC: 0.0627
- Rank ICIR: 0.675

---

### 第10步：回测分析
**文件**: `qlib/workflow/record_temp.py:PortAnaRecord`

**策略执行**: `qlib/contrib/strategy/signal_strategy.py:TopkDropoutStrategy`

**策略逻辑**:
```python
# 1. 根据模型预测得分排序
# 2. 选择 top 50 只股票
# 3. 每次调仓时随机丢弃 5 只（dropout）
# 4. 等权重配置
```

**回测引擎**: `qlib/backtest/executor.py:SimulatorExecutor`

**交易模拟**:
- 起始资金: 100,000,000 (1亿)
- 基准: 沪深300 (SH000300)
- 交易成本:
  - 开仓成本: 0.05%
  - 平仓成本: 0.15%
  - 最小成本: 5元

**回测结果**:
- 年化超额收益: 37.95% (含成本)
- 信息比率: 1.9447
- 最大回撤: -12.08%

---

### 第11步：保存结果
**文件**: `qlib/workflow/recorder.py:Recorder`

**保存到 MLflow**:
```
examples/mlruns/
  └── 866149675032491302/              # Experiment ID
      └── 6630fa79acc5413695ad9df4cd2a1c81/  # Run ID
          ├── artifacts/
          │   ├── pred.pkl             # 预测结果
          │   ├── model.pkl            # 训练好的模型
          │   ├── port_analysis_1day.pkl  # 组合分析
          │   └── indicator_analysis_1day.pkl  # 指标分析
          ├── metrics/
          │   ├── IC                   # IC值
          │   ├── ICIR                 # ICIR值
          │   ├── l2.train            # 训练损失曲线
          │   ├── l2.valid            # 验证损失曲线
          │   └── 1day.excess_return_with_cost.annualized_return
          ├── params/                  # 所有超参数
          └── meta.yaml               # 元信息
```

---

## 涉及的所有关键文件

### 配置文件
1. `examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_2020_2025.yaml` - 工作流配置

### 核心执行文件
2. `qlib/cli/run.py` - 命令行入口
3. `qlib/__init__.py` - Qlib 初始化
4. `qlib/model/trainer.py` - 训练编排

### 模型相关
5. `qlib/contrib/model/gbdt.py` - LightGBM 模型
6. `qlib/model/base.py` - 模型基类

### 数据相关
7. `qlib/contrib/data/handler.py` - Alpha158 数据处理器
8. `qlib/data/dataset/__init__.py` - DatasetH 类
9. `qlib/data/data.py` - 数据加载器
10. `qlib/data/ops.py` - 技术指标算子
11. `qlib/data/dataset/processor.py` - 数据处理器 (DropnaLabel, CSZScoreNorm)

### 回测相关
12. `qlib/contrib/strategy/signal_strategy.py` - TopkDropoutStrategy
13. `qlib/backtest/executor.py` - 回测执行器
14. `qlib/backtest/exchange.py` - 交易所模拟

### 实验追踪
15. `qlib/workflow/record_temp.py` - 记录器 (SignalRecord, SigAnaRecord, PortAnaRecord)
16. `qlib/workflow/recorder.py` - MLflow 记录器
17. `qlib/workflow/exp.py` - 实验管理

### 数据文件
18. `~/.qlib/qlib_data/cn_data/instruments/all.txt` - 股票列表
19. `~/.qlib/qlib_data/cn_data/calendars/day.txt` - 交易日历
20. `~/.qlib/qlib_data/cn_data/features/sh600000/*.bin` - 股票价格数据（二进制格式）

---

## 执行时序图

```
用户
 │
 ├─> qrun workflow_config.yaml
 │
 ▼
qlib/cli/run.py
 │
 ├─> 1. 解析 YAML 配置
 ├─> 2. 初始化 Qlib (设置数据路径、MLflow)
 │
 ▼
qlib/model/trainer.py
 │
 ├─> 3. 创建 MLflow Experiment
 ├─> 4. 创建 Recorder (Run)
 │
 ▼
创建模型和数据集
 │
 ├─> 5. 实例化 LGBModel
 ├─> 6. 实例化 DatasetH
 │    │
 │    ├─> 7. 加载 Alpha158 Handler
 │    ├─> 8. 从 .bin 文件加载数据
 │    ├─> 9. 计算 158 个技术指标
 │    └─> 10. 应用数据处理器 (Dropna, Normalize)
 │
 ▼
模型训练
 │
 ├─> 11. 准备训练/验证数据
 ├─> 12. LightGBM 训练 (310轮，第210轮最佳)
 ├─> 13. 每50轮输出日志
 │
 ▼
模型预测
 │
 ├─> 14. 在测试集上预测
 ├─> 15. 计算 IC/ICIR (0.0697 / 0.859)
 │
 ▼
回测分析
 │
 ├─> 16. TopkDropoutStrategy 生成交易信号
 ├─> 17. SimulatorExecutor 模拟交易
 ├─> 18. 计算收益、回撤等指标
 │
 ▼
保存结果
 │
 ├─> 19. 保存模型 (model.pkl)
 ├─> 20. 保存预测 (pred.pkl)
 ├─> 21. 保存回测结果 (port_analysis_1day.pkl)
 ├─> 22. 记录所有指标到 MLflow
 │
 ▼
完成
```

---

## 配置文件各部分的作用

### qlib_init
```yaml
qlib_init:
    provider_uri: "~/.qlib/qlib_data/cn_data"  # 数据路径
    region: cn                                  # 区域（中国）
```
→ 调用 `qlib.init()` 初始化

### task.model
```yaml
model:
  class: LGBModel                    # 模型类名
  module_path: qlib.contrib.model.gbdt  # 模块路径
  kwargs: {...}                      # 超参数
```
→ 动态导入并实例化 `qlib.contrib.model.gbdt.LGBModel(**kwargs)`

### task.dataset
```yaml
dataset:
  class: DatasetH
  kwargs:
    handler:
      class: Alpha158              # 数据处理器
      kwargs: {...}                # 时间范围、股票池
    segments:                      # 数据集划分
      train: [2020-01-01, 2022-12-31]
      valid: [2023-01-01, 2024-12-31]
      test: [2025-01-01, 2025-08-01]
```
→ 实例化 `DatasetH` 并准备训练/验证/测试数据

### task.record
```yaml
record:
  - class: SignalRecord      # 保存预测结果
  - class: SigAnaRecord      # 计算 IC/ICIR
  - class: PortAnaRecord     # 回测分析
```
→ 定义实验追踪的内容

---

## 性能优化点

1. **数据加载**: 使用 .bin 二进制格式，比 CSV 快 10-100 倍
2. **特征计算**: Alpha158 使用 Cython 优化的滚动窗口操作
3. **缓存机制**: `qlib/data/cache.py` 缓存计算过的特征
4. **多线程**: LightGBM 使用 20 个线程 (`num_threads: 20`)
5. **早停**: 避免过拟合，在第 210 轮停止（总共训练 310 轮）

---

## 总结

完整流程需要协调 **20+ 个 Python 文件**，涉及：
- 配置解析
- 数据加载和特征工程
- 模型训练和预测
- 回测模拟
- 实验追踪

所有结果最终保存在 `examples/mlruns/` 目录，可通过 MLflow UI 查看。

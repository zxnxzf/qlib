# Qlib 学习教程

这个目录包含了 Qlib 的入门学习脚本，从简单到复杂，帮助你逐步掌握 Qlib。

## 📚 学习路径

### 1️⃣ 基础数据访问 - `01_qlib_basics.py`
**学习内容：**
- 初始化 Qlib
- 获取交易日历
- 获取股票列表
- 获取基础行情数据（OHLCV）
- 使用表达式引擎计算技术指标
- 获取截面数据

**运行方式：**
```bash
cd examples/learn_basics
python 01_qlib_basics.py
```

### 2️⃣ 数据处理器 - `02_data_handler.py` (待创建)
**学习内容：**
- DataHandler 的使用
- Alpha158 特征集
- 数据预处理（归一化、缺失值处理）

### 3️⃣ 模型训练 - `03_model_training.py` (待创建)
**学习内容：**
- 创建数据集
- 训练模型（LightGBM）
- 模型预测

### 4️⃣ 回测分析 - `04_backtest.py` (待创建)
**学习内容：**
- 策略配置
- 回测执行
- 结果分析

## 🎯 快速开始

1. 确保已安装 Qlib 并下载数据：
```bash
pip install -e .
python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/cn_data --region cn
```

2. 按顺序运行学习脚本：
```bash
python examples/learn_basics/01_qlib_basics.py
```

## 📖 Qlib 核心概念

### 表达式引擎常用算子

**基础字段：**
- `$close`, `$open`, `$high`, `$low` - OHLC 价格
- `$volume` - 成交量
- `$factor` - 复权因子

**时间算子：**
- `Ref($close, n)` - n 天前的值
- `Mean($close, n)` - n 天移动平均
- `Std($close, n)` - n 天标准差
- `Sum($volume, n)` - n 天累加

**截面算子：**
- `Rank($close)` - 排名
- `CSRank($close)` - 截面排名

**逻辑/数学算子：**
- `If(condition, true_val, false_val)` - 条件判断
- `Max($high, $close)` - 最大值
- `Log($close)` - 对数

## 📊 如何查看实验结果（MLflow UI）

### **运行完 workflow_by_code.py 后查看图形化结果**

#### **第1步：启动 MLflow UI**

在项目根目录（`D:\code\qlib\qlib\`）执行：

```bash
# Windows 使用 qlib 虚拟环境的 Python
C:/Users/Administrator/.conda/envs/qlib/python.exe -m mlflow ui --port 5000

# 或者在激活 qlib 环境后
conda activate qlib
mlflow ui --port 5000
```

看到以下输出表示启动成功：
```
INFO:waitress:Serving on http://127.0.0.1:5000
```

---

#### **第2步：在浏览器中打开**

访问：**http://localhost:5000**

---

#### **第3步：查看实验结果**

**界面说明：**

```
┌─────────────────────────────────────────┐
│         MLflow UI 界面布局               │
├─────────────────────────────────────────┤
│                                         │
│  左侧边栏：                              │
│    - Experiments（实验列表）             │
│    - Models（模型列表）                  │
│                                         │
│  主界面：                                │
│    - Runs（运行记录列表）                │
│      └─ 每次运行 workflow_by_code.py    │
│         都会创建一条记录                 │
│                                         │
└─────────────────────────────────────────┘
```

---

#### **第4步：查看具体实验详情**

**点击某条实验记录，可以看到：**

**1. Parameters（参数）**
- 模型配置：`learning_rate`, `max_depth`, `num_leaves` 等
- 数据集配置：`topk`, `n_drop` 等

**2. Metrics（指标）**
- IC、Rank IC
- 年化收益率 `annualized_return`
- 信息比率 `information_ratio`
- 最大回撤 `max_drawdown`
- 换手率等

**3. Artifacts（结果文件）**

点击 Artifacts 标签，可以看到保存的文件：

```
artifacts/
├── pred.pkl                           # 预测结果
├── sig_analysis/                      # IC 分析
│   ├── ic.pkl                        # IC 数据
│   └── ric.pkl                       # Rank IC 数据
├── portfolio_analysis/                # 回测分析
│   ├── report_normal_1day.pkl        # 回测报告
│   ├── positions_normal_1day.pkl     # 持仓记录
│   └── port_analysis_1day.pkl        # 组合分析
└── params.pkl                         # 模型参数
```

**下载查看方法：**
- 点击文件名
- 点击右上角 **Download** 按钮
- 使用 Python 读取：
  ```python
  import pickle
  with open('pred.pkl', 'rb') as f:
      data = pickle.load(f)
  print(data.head())
  ```

---

#### **第5步：对比多个实验**

如果你运行了多次实验（调整不同参数），可以：

1. **勾选**多个实验（复选框）
2. 点击顶部的 **Compare** 按钮
3. 查看对比表格：
   - 哪个配置的 IC 最高？
   - 哪个配置的年化收益最好？
   - 哪个配置的换手率最低？

---

#### **第6步：查看图表（推荐使用 Jupyter Notebook）**

**MLflow UI 不直接显示图表，但 Jupyter Notebook 可以：**

```bash
# 启动 Jupyter Notebook
jupyter notebook examples/workflow_by_code.ipynb
```

**Jupyter 会直接显示：**
- 累积收益曲线图
- IC 时间序列图
- 风险分析图
- 分组收益对比图
- 等等...

参考：`图表含义总结.md` 了解所有图表的详细解释

---

#### **常见问题**

**Q1: 启动 MLflow UI 后，浏览器打不开？**
- 检查端口 5000 是否被占用
- 尝试换一个端口：`mlflow ui --port 5001`

**Q2: 看不到实验记录？**
- 确认在正确的目录启动（包含 `mlruns/` 的目录）
- 确认已经运行过 `workflow_by_code.py`

**Q3: 如何停止 MLflow UI？**
- 在终端按 `Ctrl + C`

**Q4: 想看图表怎么办？**
- 推荐使用 Jupyter Notebook：`jupyter notebook examples/workflow_by_code.ipynb`
- 或查看 `examples/learn_basics/图表含义总结.md`

---

## 🔗 相关资源

- [Qlib 官方文档](https://qlib.readthedocs.io/)
- [GitHub 仓库](https://github.com/microsoft/qlib)
- 完整示例：`examples/workflow_by_code.py`
- Jupyter 教程：`examples/workflow_by_code.ipynb`
- 图表详解：`examples/learn_basics/图表含义总结.md`

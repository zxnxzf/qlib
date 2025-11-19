# 如何查看 Qlib 实验结果

运行完 `workflow_by_code.py` 后，实验结果保存在 `mlruns/` 目录。有三种查看方式：

## 方式1：直接查看文件（文本方式）

```bash
# 查看目录结构
ls -R mlruns/

# 重要文件：
# - pred.pkl: 模型预测结果
# - params.pkl: 训练好的模型
# - sig_analysis/ic.pkl: IC 分析结果
# - portfolio_analysis/: 回测结果
```

可以用 Python 读取：
```python
import pickle
import pandas as pd

# 读取预测结果
with open('mlruns/.../artifacts/pred.pkl', 'rb') as f:
    pred = pickle.load(f)
print(pred)

# 读取 IC 结果
with open('mlruns/.../artifacts/sig_analysis/ic.pkl', 'rb') as f:
    ic = pickle.load(f)
print(ic)
```

## 方式2：MLflow UI（图形界面，推荐！）⭐

### 启动 MLflow UI

在 qlib 项目根目录运行：

```bash
# 激活 qlib 环境
conda activate qlib

# 启动 MLflow UI
mlflow ui
```

默认会在 `http://localhost:5000` 启动

### 在浏览器中查看

打开浏览器访问 `http://localhost:5000`，你会看到：

#### 主界面功能：
1. **实验列表**
   - 查看所有实验运行记录
   - 每个实验的开始时间、持续时间、状态

2. **参数对比**
   - 查看模型超参数
   - 对比不同实验的参数差异

3. **指标对比**
   - IC (Information Coefficient)
   - 收益率
   - 夏普比率
   - 等等...

4. **可视化**
   - 参数重要性图
   - 指标变化曲线
   - 对比多个实验

5. **下载文件**
   - 下载模型文件
   - 下载预测结果
   - 下载回测报告

### MLflow UI 常用操作：

```bash
# 指定端口启动
mlflow ui --port 8080

# 指定 mlruns 目录
mlflow ui --backend-store-uri /path/to/mlruns
```

## 方式3：Jupyter Notebook（交互式分析）

### 安装 Jupyter

```bash
conda activate qlib
pip install jupyter
pip install -e .[analysis]  # 安装分析依赖
```

### 启动 Jupyter

```bash
# 在 examples 目录启动
cd examples
jupyter notebook
```

浏览器会自动打开 Jupyter 界面。

### 创建分析脚本

新建一个 Notebook，运行以下代码：

```python
import pickle
import pandas as pd
import matplotlib.pyplot as plt

# 1. 读取实验记录
from qlib.workflow import R

# 列出所有实验
experiments = R.list_experiments()
print(experiments)

# 2. 读取特定实验的结果
experiment_id = "实验ID"  # 从 mlflow ui 或 mlruns/ 获取
recorder = R.get_recorder(experiment_id=experiment_id, experiment_name="workflow")

# 3. 加载预测结果
pred = recorder.load_object("pred.pkl")
print(pred.head())

# 4. 加载 IC 分析
ic = recorder.load_object("sig_analysis/ic.pkl")
print(f"平均IC: {ic.mean()}")

# 5. 绘制 IC 时间序列
ic.plot(figsize=(15, 5), title='IC Over Time')
plt.show()

# 6. 加载回测结果
report = recorder.load_object("portfolio_analysis/report_normal_1day.pkl")
print(report)

# 7. 绘制累积收益曲线
report['return'].cumsum().plot(figsize=(15, 5), title='Cumulative Return')
plt.show()
```

### 现成的 Notebook

Qlib 提供了现成的分析 Notebook：

```bash
# 打开示例 Notebook
jupyter notebook examples/workflow_by_code.ipynb
```

这个 Notebook 包含：
- ✅ 完整的工作流代码
- ✅ 信号分析图表
- ✅ 回测结果可视化
- ✅ IC/RankIC 分析
- ✅ 收益分布图

## 快速对比

| 方式 | 优点 | 缺点 | 适用场景 |
|------|------|------|----------|
| **直接查看文件** | 简单直接 | 不直观，需要写代码 | 快速检查文件是否生成 |
| **MLflow UI** ⭐ | 界面友好，功能强大 | 需要启动服务 | 对比实验、查看指标 |
| **Jupyter Notebook** | 交互式，灵活 | 需要写代码 | 深度分析、自定义可视化 |

## 推荐工作流

1. **运行实验**
   ```bash
   python examples/workflow_by_code.py
   ```

2. **MLflow UI 快速查看**
   ```bash
   mlflow ui
   # 打开 http://localhost:5000
   ```

3. **Jupyter 深度分析**（可选）
   ```bash
   jupyter notebook examples/workflow_by_code.ipynb
   ```

## 关键指标说明

### IC (Information Coefficient)
- **含义**：预测值与真实收益的相关系数
- **范围**：-1 到 1
- **好的IC**：通常 > 0.03 就不错，> 0.05 很好
- **查看位置**：MLflow UI 的 Metrics 或 `sig_analysis/ic.pkl`

### RankIC
- **含义**：预测排名与真实收益排名的相关系数
- **优点**：比 IC 更稳定，不受极值影响

### 回测指标
- **年化收益率 (Annualized Return)**
- **夏普比率 (Sharpe Ratio)**：收益风险比
- **最大回撤 (Max Drawdown)**：最大损失
- **信息比率 (Information Ratio)**：超额收益的稳定性

## 常见问题

**Q: MLflow UI 启动失败？**
```bash
# 检查端口是否被占用
netstat -ano | findstr :5000

# 换个端口
mlflow ui --port 8080
```

**Q: 找不到实验数据？**
```bash
# 确认 mlruns 目录存在
ls mlruns/

# 检查实验名称
python -c "from qlib.workflow import R; print(R.list_experiments())"
```

**Q: Jupyter 中导入 qlib 失败？**
```bash
# 确保在正确的环境中
conda activate qlib

# 重新安装
pip install -e .
```
 
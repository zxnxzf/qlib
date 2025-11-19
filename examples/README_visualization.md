# Qlib Results Visualization Tool

## 概述

`visualize_qlib_results.ipynb` 是一个独立的Jupyter Notebook，专门用于可视化Qlib实验结果。它完全按照 `workflow_by_code.ipynb` 的标准实现，确保与Qlib原生功能完全兼容。

## 主要优势

✅ **完全标准** - 直接使用Qlib的标准可视化函数
✅ **简单易用** - 只需要实验ID和记录ID
✅ **避免重复工作** - 不需要重新实现可视化逻辑
✅ **确保兼容性** - 使用Qlib原生功能，无兼容性问题
✅ **完整功能** - 包含所有标准的量化投资分析图表

## 支持的图表类型

### 1. Position Analysis (投资组合分析)
- **Portfolio Report Graphs** - 投资组合表现报告
- **Risk Analysis Graphs** - 风险分析图表
- **Score IC Graphs** - IC相关性分析

### 2. Model Analysis (模型性能分析)
- **Model Performance Graphs** - 模型性能图表

## 使用方法

### 步骤1: 准备实验信息

从 `examples/mlruns/` 目录中获取以下信息：
- **实验ID** (experiment_id) - 实验目录名称（如 `866149675032491302`）
- **记录ID** (recorder_id) - 运行记录ID（如 `3d0e78192f384bb881c63b32f743d5f8`）

**您的最新实验信息**：
```bash
# 从目录结构中获取
experiment_id = "866149675032491302"  # 实验ID
recorder_id = "3d0e78192f384bb881c63b32f743d5f8"  # 记录ID
```

**重要提示**：
- 使用 `experiment_id` 而不是 `experiment_name` 来避免MLflow目录结构问题
- Notebook现在支持智能时间范围检测，会自动匹配预测数据的时间范围
- 如果遇到数据不匹配问题，请检查Qlib数据是否包含对应时间范围的数据

### 步骤2: 配置Notebook

打开 `examples/visualize_qlib_results.ipynb`，在设置区域填入你的信息：

```python
# 设置实验信息（使用您最新的实验）
experiment_id = "866149675032491302"  # 您的实验ID
recorder_id = "3d0e78192f384bb881c63b32f743d5f8"  # 您的记录ID

# 可选：训练实验信息
train_experiment_id = "train_model"
train_recorder_id = None  # 训练模型的记录ID
```

### 步骤3: 运行Notebook

在 `examples/` 目录下启动Jupyter Notebook：
```bash
cd examples
jupyter notebook visualize_qlib_results.ipynb
```

然后依次运行所有单元格，系统会：
1. 加载实验数据
2. 生成标准的Qlib可视化图表
3. 显示数据概览和诊断信息

### 文件位置优势

✅ **与MLflow同目录** - 直接访问 `examples/mlruns/` 数据
✅ **无路径问题** - Qlib自动查找当前目录下的mlruns
✅ **标准项目结构** - 符合Qlib示例目录规范

## 获取实验ID的方法

### 方法1: 从MLflow UI
1. 启动MLflow: `mlflow ui`
2. 在浏览器中打开UI
3. 找到你的实验
4. 点击具体的运行记录
5. 在URL或页面信息中找到实验ID和记录ID

### 方法2: 从命令行
```bash
# 查看所有实验
mlflow experiments list

# 查看特定实验的运行
mlflow runs list --experiment-id <experiment_id>
```

### 方法3: 从我们之前的脚本
运行 `recorder_visualizer_from_path.py` 时，日志中会显示相关信息。

## 文件结构要求

确保你的artifacts目录包含以下文件：
```
artifacts/
├── pred.pkl
├── portfolio_analysis/
│   ├── report_normal_1day.pkl
│   ├── positions_normal_1day.pkl
│   └── port_analysis_1day.pkl
└── other files...
```

## 故障排除

### 常见问题

1. **"recorder not found"错误**
   - 检查实验名称和记录ID是否正确
   - 确认MLflow服务器正在运行

2. **"artifacts file not found"错误**
   - 确保实验已完成并生成了artifacts
   - 检查文件路径是否正确

3. **图表显示异常**
   - 尝试重启Jupyter内核
   - 检查数据质量

4. **Qlib初始化问题**
   - 确保Qlib数据已正确下载
   - 检查数据路径配置

### 调试功能

Notebook包含完整的诊断功能，会显示：
- 数据加载状态
- 数据形状和内容
- 错误信息和详细提示

## 与其他工具的对比

| 特性 | visualize_qlib_results.ipynb | recorder_visualizer_from_path.py |
|------|----------------------------|--------------------------------|
| 数据来源 | MLflow记录 | 直接artifacts路径 |
| 标准兼容性 | ✅ 100%标准 | ⚠️ 自定义实现 |
| 易用性 | 🔧 需要配置 | ✅ 一键运行 |
| 图表类型 | ✅ 完整 | ✅ 大部分 |
| 维护成本 | 🔧 需要更新 | ✅ 自动适配 |

## 使用建议

1. **新用户**: 建议使用 `visualize_qlib_results.ipynb`，确保与Qlib标准完全一致
2. **快速查看**: 使用 `recorder_visualizer_from_path.py`，适合快速生成HTML报告
3. **深入分析**: 两者结合使用，获得最全面的分析结果

## 技术细节

### 依赖的Qlib模块
```python
from qlib.workflow import R
from qlib.contrib.report import analysis_model, analysis_position
from qlib.data import D
```

### 核心数据加载逻辑
```python
recorder = R.get_recorder(recorder_id=recorder_id, experiment_name=experiment_name)
pred_df = recorder.load_object("pred.pkl")
report_normal_df = recorder.load_object("portfolio_analysis/report_normal_1day.pkl")
analysis_df = recorder.load_object("portfolio_analysis/port_analysis_1day.pkl")
```

### 标准可视化调用
```python
analysis_position.report_graph(report_normal_df)
analysis_position.risk_analysis_graph(analysis_df, report_normal_df)
```

### 🔧 常见问题解决

#### **IC分析显示"数据缺失"怎么办？**

**问题原因**：数据集时间范围配置与预测数据不匹配

**解决方案**：
1. Notebook现在支持智能时间范围检测，会自动识别预测数据的时间范围
2. 如果仍然失败，请检查：
   - Qlib数据是否包含对应年份的数据
   - 股票池配置是否一致（csi300 vs all）
   - 数据路径是否正确

**手动修复方法**：
```python
# 在步骤3中手动配置数据集时间范围
dataset_config = {
    "kwargs": {
        "handler": {
            "kwargs": {
                "start_time": "2025-01-01",  # 匹配你的数据开始时间
                "end_time": "2025-12-31",    # 匹配你的数据结束时间
                "instruments": "all",        # 尝试使用"all"替代"csi300"
            }
        },
        "segments": {
            "test": ("2025-01-01", "2025-08-01")  # 精确匹配预测数据时间
        }
    }
}
```

#### **数据格式问题**

如果遇到数据合并问题，请检查：
1. 预测数据是否为MultiIndex格式 (datetime, instrument)
2. 列名是否为'score'
3. 数据是否包含缺失值

#### **时间范围不匹配**

诊断信息会显示：
```
📅 时间范围匹配检查:
   预测数据: 2025-01-02 至 2025-08-01
   标签数据: 2025-01-01 至 2025-08-01
```

确保两个时间范围基本一致即可。

---

**注意**: 这个工具直接使用Qlib的标准功能，确保与 `workflow_by_code.ipynb` 的结果完全一致。如果遇到直线显示问题，请检查数据质量，而不是工具实现问题。

**更新说明**：现在支持智能时间范围检测，自动匹配预测数据的时间范围，大大减少了配置错误的可能性。
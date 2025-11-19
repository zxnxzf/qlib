# Qlib 实盘预测系统使用说明

## 概述

本系统基于已训练的LightGBM模型，提供每日股票预测功能，生成iQuant可用的交易信号。

## 文件说明

### 1. daily_prediction.ipynb
Jupyter Notebook版本，包含详细的步骤说明和可视化输出。

### 2. daily_predict.py
Python脚本版本，适合命令行执行和自动化部署。

## 快速开始

### 方法一：使用Jupyter Notebook
```bash
cd examples
jupyter notebook daily_prediction.ipynb
```

依次执行所有单元格即可完成预测。

### 方法二：使用Python脚本
```bash
cd examples
python daily_predict.py
```

脚本会自动完成所有步骤并生成预测文件。

## 配置参数

在开始预测前，需要修改以下参数：

```python
# 原实验信息
experiment_id = "866149675032491302"  # 你的实验ID
recorder_id = "3d0e78192f384bb881c63b32f743d5f8"  # 你的记录ID

# 预测参数
top_k = 50  # 推荐股票数量
weight_method = "equal"  # 权重分配: "equal" 或 "score"
min_score_threshold = 0.0  # 最低分数阈值
```

## 输出文件

预测完成后，会在 `./predictions/` 目录下生成以下文件：

### 1. CSV文件
```
prediction_results_YYYYMMDD.csv
```
字段说明：
- `instrument`: 股票代码
- `datetime`: 预测日期
- `score`: 预测分数
- `target_weight`: 目标权重
- `prediction_date`: 预测生成日期
- `generated_time`: 生成时间戳

### 2. Excel文件
```
prediction_results_YYYYMMDD.xlsx
```
包含两个工作表：
- `预测结果`: 同CSV格式的预测数据
- `统计信息`: 预测结果的统计摘要

### 3. 日志文件
```
prediction_log_YYYYMMDD.txt
```
包含执行过程的详细记录。

## iQuant集成

### 读取预测结果
```python
import pandas as pd

# 读取预测结果
pred_df = pd.read_csv('./predictions/prediction_results_YYYYMMDD.csv')

# 按权重分配仓位
for _, row in pred_df.iterrows():
    stock_code = row['instrument']
    weight = row['target_weight']
    score = row['score']

    # 执行交易逻辑
    print(f"股票: {stock_code}, 权重: {weight:.4f}, 分数: {score:.6f}")
```

### 交易执行建议
1. **权重分配**: 按`target_weight`字段分配资金
2. **优先级**: 按`score`字段排序确定优先级
3. **风险控制**: 建议设置最大单股权重限制
4. **流动性**: 考虑股票流动性和交易成本

## 定时执行

### Linux/Mac定时任务
```bash
# 编辑crontab
crontab -e

# 添加每日收盘后执行
0 16 * * 1-5 cd /path/to/qlib/examples && python daily_predict.py
```

### Windows定时任务
```bat
# 创建批处理文件 predict.bat
@echo off
cd /d D:\code\qlib\qlib\examples
python daily_predict.py
```

然后在Windows任务计划程序中设置定时执行。

## 故障排除

### 1. 模型加载失败
**问题**: 无法找到模型文件
**解决**: 检查`experiment_id`和`recorder_id`是否正确

### 2. 数据加载失败
**问题**: Qlib数据缺失
**解决**: 确保Qlib数据路径正确且数据已更新到最新

### 3. 预测结果为空
**问题**: 指定日期无数据
**解决**: 检查预测日期是否为交易日，或调整数据范围

### 4. 权重分配异常
**问题**: 权重总和不等于1
**解决**: 检查分数计算逻辑，确保数值有效性

## 性能优化

### 1. 数据缓存
Qlib会自动缓存计算的特征，提高后续预测速度。

### 2. 并行处理
可以考虑对多个日期进行批量预测：
```python
# 批量预测多日
dates = ['2025-01-03', '2025-01-06', '2025-01-07']
for date in dates:
    prediction_date = date
    # 执行预测逻辑
```

### 3. 内存管理
处理大量数据时，注意内存使用，及时释放不需要的变量。

## 风险提示

1. **模型风险**: 模型基于历史数据训练，未来表现可能不同
2. **数据风险**: 确保使用最新的、准确的行情数据
3. **执行风险**: 实际交易中考虑滑点、延迟等因素
4. **监管风险**: 遵守相关法规和交易规则

## 联系支持

如遇到技术问题，请检查：
1. Qlib版本是否兼容
2. 数据路径是否正确
3. 实验ID和记录ID是否准确
4. 系统环境是否完整

更多技术细节请参考Qlib官方文档。
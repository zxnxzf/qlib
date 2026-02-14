# Manual Trading Script Design

## Goal
日频手工交易流程：当天运行脚本，使用上一交易日数据生成信号，输出订单 CSV 供人工下单。

## Inputs
- 交易日历：`examples/custom/trade_calendar_base.csv`（周一到周五，2026 年）
- 持仓/现金 CSV：`code,position`（示例：`CASH,500000`）
- 训练记录：`experiment_id` + `recorder_id`
- Qlib 数据路径：`provider_uri`

## Outputs
- 订单 CSV：`order_id,stock,action,shares,price,amount,score,weight`
- 控制台摘要：trade_date、pred_date、总资产、可用资金、买卖金额等
- PnL 曲线：`examples/custom/pnl_history.csv`（date,total_asset,daily_pnl,daily_return,cum_return）

## Flow
```mermaid
flowchart TD
A[启动脚本] --> B[读取配置]
B --> C[读取本地交易日历CSV]
C --> D{今天是否在日历中}
D -- 否 --> E[打印警告并退出]
D -- 是 --> F[根据日历取上一交易日作为 required_pred_date]
F --> G[检查是否需要更新Qlib数据]
G --> H{是否过期或缺失}
H -- 否 --> I[初始化Qlib]
H -- 是 --> J[下载并更新数据]
J --> I
I --> M[从Qlib日历取最后可用日期作为 pred_date]
M --> N[加载实验与模型]
N --> O[构建数据集并生成预测]
O --> P[整理预测分数与权重]
P --> Q[读取持仓与现金CSV]
Q --> R[用 pred_date 收盘价获取价格]
R --> S[筛选 TopK 并重新计算权重]
S --> T[计算目标仓位与调仓数量]
T --> U[生成订单 买入和卖出]
U --> V[输出订单CSV并打印汇总]
V --> W[输出下一个持仓并更新 PnL 曲线]
W --> X[人工下单]
```

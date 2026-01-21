# 手工日频交易脚本设计

## 目标
- 单次执行：当天运行脚本，使用上一交易日数据生成信号。
- 仅用 Qlib 数据（不依赖 iQuant 实时报价）。
- 输出订单 CSV 供人工下单。

## 核心流程
1. 读取脚本内 `DEFAULT_CONFIG`。
2. 从 `trade_calendar_base.csv` 判断今天是否交易日；非交易日直接退出。
3. 依据本地日历确定上一交易日 `required_pred_date`。
4. 执行社区数据更新检查；若更新失败，直接退出。
5. 初始化 Qlib，从本地数据日历取最后可用日期作为 `pred_date`。
6. 加载模型与数据集，生成预测分数与权重。
7. 读取持仓/现金 CSV（原始股数）。
8. 用 `pred_date` 的 `$factor` 将持仓转换为复权股数。
9. 使用 Qlib `Exchange + OrderGenWOInteract` 生成订单（复权）。
10. 输出订单 CSV，并附加不复权价格/股数。

## 输入/输出
### 持仓/现金 CSV（输入）
```
code,position
CASH,500000
SH600000,1000
SZ000001,500
```

### 订单 CSV（输出）
字段：
`order_id,stock,action,shares,price,amount,score,weight,price_raw,shares_raw,amount_raw`

说明：
- `price/shares/amount`：复权口径（Qlib 交易模块）
- `price_raw/shares_raw/amount_raw`：原始口径（下单参考）

## 关键逻辑
### 数据更新
- 沿用 `live_daily_predict.py` 的数据更新逻辑。
- 更新失败直接退出（不交易）。

### 交易日与预测日
- `trade_date` 默认当天。
- `pred_date` 使用 Qlib 本地数据日历的最后可用交易日。

### T+1（hold_thresh=1）
- 维护 `holdings_history.json`（自动记录买入日期）。
- 持有天数按 `trade_calendar_base.csv` 交易日计数。
- 未达持有天数的卖单直接剔除。

### 复权/不复权处理
- Qlib 订单生成基于复权价格与复权股数。
- 持仓输入为原始股数，按 `pred_date` 的 `$factor` 转复权。
- 输出时附加原始价格与原始股数：
  - `price_raw = price_adj / factor`
  - `shares_raw = shares_adj * factor`（并按 100 股向下取整）

## 异常处理
- 非交易日：警告并退出。
- 更新失败：错误并退出。
- 预测为空：警告并退出。
- 因子或价格缺失：跳过对应股票并告警。

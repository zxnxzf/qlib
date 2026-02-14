# Manual Trading PnL Tracking Design (2026-02-13)

## Goal
在 `examples/custom/manual_daily_trade.py` 增加累计收益记录。脚本每天运行一次，基于订单与 Qlib 收盘价估算当日资产并输出累计收益曲线。

## Requirements
- 输出文件固定为 `examples/custom/pnl_history.csv`，同日重复运行覆盖该日期记录。
- 估值使用“下单后持仓”（`positions_next`）+ 现金。
- 记录日期使用 `trade_date`；估值日期使用 `trade_base_date`（若 Qlib 最新数据不足会回退）。
- 指标：`date,total_asset,daily_pnl,daily_return,cum_return`。
- 使用 Qlib 收盘价估算，价格缺失时给出 warning 并按 0 估值。

## Data Flow
1. 生成订单并写入 `orders_out`。
2. 计算 `positions_next` 并落盘。
3. 用 `positions_next` + 当日（或最近可用）收盘价估算 `total_asset`。
4. 追加/覆盖 `pnl_history.csv` 当日记录并重算整表收益率。

## Calculations
- `total_asset = cash + Σ( raw_shares × raw_price )`，其中 `raw_price = adj_close / factor`。
- `daily_pnl = total_asset_today - total_asset_prev`。
- `daily_return = daily_pnl / total_asset_prev`。
- `cum_return = total_asset_today / total_asset_first - 1`。

## Edge Cases
- 文件不存在：自动创建。
- 当天重复运行：覆盖并重算收益率。
- `trade_date` 超过最新数据：估值日期回退到可用日期，但记录日期仍为 `trade_date`。

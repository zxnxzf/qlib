# 手动实盘（manual_daily_trade）使用说明

本文说明如何用 `examples/custom/manual_daily_trade.py` 进行日频手动实盘，并保证与
`benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_2020_2025.yaml` 的回测配置一致。

## 1. 用途与一致性说明

- 本脚本用于“预测 + 生成手动下单订单 + 维护持仓”。
- 对齐回测（workflow）的关键点：
  - 标的池、数据处理窗口、策略参数与回测保持一致。
  - 预测日 `pred_date` 采用 T+1 逻辑（用上一交易日数据）。
- 对比结果（链式三天，1/3、1/6、1/7）：
  - 差异仅为 **1 股级别的取整误差**（后复权 -> 未复权换算取整）
  - 现金差异接近 0

## 2. 日常实盘步骤

1. **更新持仓与现金**
   - 文件：`examples/custom/positions_manual.csv`
   - 格式示例：
     ```csv
     code,position
     CASH,50000
     SH600000,100
     SZ000001,200
     ```

2. **运行脚本生成订单**
   ```bash
   python /qlib/examples/custom/manual_daily_trade.py
   ```

3. **查看输出**
   - 订单：`examples/custom/orders_manual.csv`
   - 控制台也会打印买卖列表

4. **手动下单**
   - 价格为未复权价格，直接用于真实下单参考。

5. **更新下一日持仓**
   - 自动生成：`examples/custom/positions_manual_next.csv`
   - 确认成交后：
     ```bash
     mv /qlib/examples/custom/positions_manual_next.csv /qlib/examples/custom/positions_manual.csv
     ```

6. **次日重复**

## 3. 关键配置位置

脚本配置集中在 `manual_daily_trade.py` 的 `DEFAULT_CONFIG`：

- 交易日历：`DEFAULT_CONFIG["calendar"]`
- 实盘路径：`DEFAULT_CONFIG["paths"]`
- 预测/策略对齐：`DEFAULT_CONFIG["workflow_alignment"]`
- 持仓约束（持有天数）：`DEFAULT_CONFIG["strategy"]["hold_thresh"]`
- 并行加速：`DEFAULT_CONFIG["qlib_init"]["kernels"]` / `joblib_backend`

## 4. 对比 workflow 的方式（简述）

我们使用链式对比脚本验证一致性：

```bash
python /qlib/examples/custom/compare_live_chain.py
```

输出目录：
- `examples/custom/compare_live_chain_YYYY-MM-DD_YYYY-MM-DD/`
- 其中 `compare.log` 记录逐日 diff 统计。

结论：实盘模式与 workflow 回测**一致**，仅存在 1 股级别取整差异。

## 5. 持仓历史文件说明

- 文件：`examples/custom/holdings_history_manual.json`
- 用途：用于计算持仓天数，配合 `hold_thresh` 限制卖出。
- 日常无需手动修改，如需重置持仓天数，可删除该文件。

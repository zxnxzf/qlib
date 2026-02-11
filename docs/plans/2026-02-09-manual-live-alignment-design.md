# Manual Live Alignment Design

## 目标
在 **不依赖 workflow 回测产物**（`pred.pkl` / positions / 回测窗口）的前提下，让 `manual_daily_trade.py` 的预测与 workflow 尽量对齐，并提供“纯现金起步”的自动初始化能力。

## 约束
- live 模式不能开启 `use_position_count` 和 `use_backtest_window`。
- 预测必须使用实时计算（不读取 `pred.pkl`）。
- 初始持仓为空，仅现金 5 万。

## 方案一：预测配置对齐（推荐）
- 新增 `workflow_alignment.workflow_config_path` 指向 workflow YAML。
- 读取 YAML 的 `data_handler_config`，将以下字段注入到 handler：
  - `start_time`
  - `fit_start_time`
  - `fit_end_time`
  - `instruments`
- `end_time` 采用 **pred_date**（避免未来数据泄露），并在日志中提示：
  - `handler_end_time_policy=pred_date`
- 如果 YAML 读取失败，回退到脚本内默认配置并记录警告。

## 方案二：初始持仓自动初始化
- 当 `positions_manual.csv` 不存在时，自动创建并写入：
  - `CASH=50000`
- 当 `holdings_history_manual.json` 不存在时，自动创建 `{}`。
- 执行时打印提示日志，例如：
  - `auto init positions: CASH=50000`
  - `auto init holdings_history: {}`

## 数据流说明
1. 读取 workflow YAML（如可用）生成 handler 配置。
2. `end_time` 强制设为 `pred_date`。
3. 若 `positions_manual.csv` / `holdings_history_manual.json` 不存在，自动生成。
4. 计算预测 → 生成订单 → 更新持仓与历史。

## 影响与风险
- 预测对齐度显著提升，但仍可能因实盘安全策略（`end_time=pred_date`）产生轻微差异。
- 如果 `pred_date` 为本地最后一个交易日，标签缺失可能导致预测为空（需日志提示）。

## 测试建议
- 用 2025-01-06 ~ 2025-01-08 做对比：
  - 确认预测分数 / TopK 接近 workflow。
  - 观察差异是否显著收敛。

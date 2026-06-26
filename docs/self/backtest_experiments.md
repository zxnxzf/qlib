# Backtest Experiments Log

本文档用于记录「回测阶段做过的实验、配置改动、结果与结论」，避免重复试错。

## 记录规范

- 每个实验都要有唯一编号（`EXP-YYYYMMDD-XX`）
- 必须包含：目标、改动项、对照方式、核心指标、结论
- 明确标记是否进入实盘：`Adopted` / `Not Adopted`

---

## EXP-20260227-01 月度 pos_ratio 仓位开关

- Date: 2026-02-27
- Status: Completed
- Decision: Not Adopted（不接入 manual 实盘）

### 目标

验证“月度仓位缩放（`pos_ratio`）”是否能提升策略稳健性（重点看含成本超额收益与 IR）。

### 代码/配置改动

- 策略侧增加开关（默认关闭）：
  - `enable_monthly_pos_ratio`
  - `monthly_pos_ratio_benchmark`
  - `monthly_pos_ratio_state_path`
- 仅在 `enable_monthly_pos_ratio: true` 时生效，默认回测逻辑不变。

### 对照设计

- 2025 测试段：
  - OFF: `workflow_lightgbm_wf_valid_2021_2023_valid2024_test2025.yaml`
  - ON: `workflow_lightgbm_wf_valid_2021_2023_valid2024_test2025_posratio_on.yaml`
- 2024 测试段：
  - OFF: `workflow_lightgbm_wf_valid_2020_2022_valid2023_test2024.yaml`
  - ON: `workflow_lightgbm_wf_valid_2020_2022_valid2023_test2024_posratio_on.yaml`

### 核心结果

#### 2025（OFF vs ON）

| Metric | OFF | ON |
|---|---:|---:|
| IC | 0.0574315716 | 0.0574315725 |
| ICIR | 0.7855578583 | 0.7855578784 |
| Excess Return With Cost (Annualized) | 0.431233 | 0.417326 |
| Excess IR With Cost | 2.552304 | 2.417839 |
| Max Drawdown With Cost | -0.099455 | -0.105177 |

#### 2024（OFF vs ON）

| Metric | OFF | ON |
|---|---:|---:|
| IC | 0.0803483268 | 0.0803483234 |
| ICIR | 0.8731497852 | 0.8731492554 |
| Excess Return With Cost (Annualized) | -0.031337 | -0.114377 |
| Excess IR With Cost | -0.133857 | -0.505430 |
| Max Drawdown With Cost | -0.278479 | -0.261746 |

### 结论

- 该开关在 2024/2025 两个测试段均未带来稳定收益改进。
- 2025：收益、IR、回撤均变差。
- 2024：回撤略有改善，但收益与 IR 明显变差。
- 因此该功能保留为实验开关，默认关闭，不纳入实盘主流程。

### 相关产物

- 2025 日志：
  - `examples/custom/qlib_workflows_generated_v2/run_2025_off.log`
  - `examples/custom/qlib_workflows_generated_v2/run_2025_on.log`
- 2024 日志：
  - `examples/custom/qlib_workflows_generated_v2/run_2024_off.log`
  - `examples/custom/qlib_workflows_generated_v2/run_2024_on.log`
- 状态文件（ON 测试）：
  - `examples/custom/pos_ratio_state_2025_on.json`
  - `examples/custom/pos_ratio_state_2024_on.json`

---

## EXP-20260228-01 ST/*ST 股票过滤开关（TopkDropoutStrategy）

- Date: 2026-02-28
- Status: Completed
- Decision: Pending Data Source（逻辑已接入，待可用 ST 数据源）

### 目标

在 2025 workflow 回测中增加一个开关：买入候选若为 `ST/*ST` 则不选。

### 代码/配置改动

- `TopkDropoutStrategy` 新增参数：
  - `enable_st_stock_filter`
  - `st_filter_field`（默认 `$is_st`）
  - `st_name_field`（默认 `$name`）
  - `st_name_pattern`（默认 `^\*?ST`）
  - `st_filter_strict`
- 新增 `STStockFilterManager`：
  - 优先读取 `st_filter_field`
  - 回退读取 `st_name_field + regex`
  - 仅作用于买入候选，不改卖出逻辑
- 2025 workflow 增加开关字段：
  - `workflow_lightgbm_wf_valid_2021_2023_valid2024_test2025.yaml`
- 新增开关 ON 示例：
  - `workflow_lightgbm_wf_valid_2021_2023_valid2024_test2025_stfilter_on.yaml`

### 运行结果

- 回测可正常跑通（`qrun ..._stfilter_on.yaml`）
- 日志提示：
  - `[STFilter] enabled but no usable ST data source was found ... fallback to no filtering.`
- 在当前 `~/.qlib/qlib_data/cn_data` 上，`$is_st/$name` 无有效可用数据，因此过滤逻辑降级为不生效。

### 结论

- 开关与实现已完成，且不影响默认流程（默认关闭）。
- 要让过滤真正生效，需要可用的 ST 数据源（例如可查询的 `$is_st` 字段或可用股票名称字段）。

# TODO：低回撤优先的 Topk/LiveTopk 风控策略

> 目标：在现有 LightGBM + Alpha158 + Topk/LiveTopk 的基础上，增加一层“绝对回撤优先”的风险控制逻辑，在控制自身净值回撤的前提下尽量追求较高收益。

## 1. 总回撤驱动的仓位控制（组合层面）

- 思路：监控策略自身的净值曲线，基于“从历史最高点的回撤”动态调整整体 `risk_degree`。
- 初版规则设想：
  - 记录历史最高净值 `nav_max`，每个交易日更新当前净值 `nav_cur`；
  - 计算回撤 `dd = 1 - nav_cur / nav_max`；
  - 当 `dd` 超过阈值（如 10%/15%）时，将 `risk_degree` 从 0.95 降到 0.3/0.1，进入防守模式；
  - 当 `nav_cur` 再创新高或回撤收窄到某个范围内时，逐步恢复 `risk_degree`。
- 技术落点：
  - 针对 `TopkDropoutStrategy` / `LiveTopkStrategy`，在子类中重写 `get_risk_degree(trade_step)`；
  - 需要能访问上一日的组合净值（可从 `portfolio_metrics` 或 `Position.now_account_value` 中间层缓存中提取）。

## 2. 单票级风控（个股仓位与止损）

- 单票上限：
  - 限制单只股票的目标资金占比不超过总资产的某个比例（如 3%–5%），避免个股踩雷拖累整体回撤；
  - 在 `LiveTopkStrategy` Round 2 买入逻辑中，对每只股票的目标资金 `target_value` 做 `min(target_value, max_stock_weight * total_value)` 截断。
- 简单止损：
  - 跟踪每只持仓的成本价/最高价格，设定固定止损阈值（如从成本价跌 20%/30% 强制卖出）；
  - 在生成卖出列表时，加入“符合止损条件的股票”作为强制卖出候选。
- 技术落点：
  - 利用 `Position` 中的成本信息（若不完备，可在策略层自己维护一个 `{stock_id: buy_price}` 的状态字典）；
  - 在 `generate_trade_decision` 中补充“风险驱动的卖出列表”，与 Topk/Dropout 逻辑合并。

## 3. 调仓频率与噪音过滤

- 降低调仓频率：
  - 例如只在每周第一个交易日/每两周调仓，其他交易日 `generate_trade_decision` 直接返回空决策；
  - 减少因短期噪音导致的频繁换仓和回撤放大。
- 简单信号过滤：
  - 只有当候选股票之间的得分差异足够大时才触发换仓（例如新进 topk 的分数与被替换股票分数差异 > 某阈值）。
- 技术落点：
  - 利用 `trade_calendar.get_trade_step()` 判断当前是第几天，基于模运算控制调仓频率；
  - 在 Topk/LiveTopk 的“卖出/买入选择环节”加入基于得分差异的过滤条件。

## 4. 评估与对比框架

- 新增专用 workflow 配置：
  - `examples/benchmarks/LightGBM/workflow_config_lightgbm_LowDD_Alpha158_2020_2025.yaml`
  - 策略指向新风控版 LiveTopk 子类（例如 `LiveTopkLowDDStrategy`）。
- 对比对象：
  - 原版 `TopkDropoutStrategy`；
  - 原版 `LiveTopkStrategy`；
  - 带动态仓位/限仓的 `LiveTopkDynamicRiskStrategy`（当前实现）。
- 指标关注：
  - 年化收益、最大回撤、波动率、夏普/信息比、换手率；
  - 尤其关注“最大回撤显著降低、收益在可接受范围内”的参数区间。

## 5. 实施顺序建议

1. 在现有 `LiveTopkDynamicRiskStrategy` 基础上，新增“总回撤驱动的 get_risk_degree”版本（组合层次控制）。
2. 然后叠加单票限仓 + 止损规则，观察对回撤与收益的边际影响。
3. 最后根据回测结果调参（回撤阈值、止损比例、max_stock_weight、调仓频率等），形成一套稳定配置。

---

# TODO：基于 RD-Agent-Quant 的自动因子/模型优化集成

> 目标：在 qlib 现有回测/数据框架之上，引入 RD-Agent-Quant，自动化部分“因子挖掘 + 模型调参 + 策略评估”流程，用于辅助个人策略研发。

## 1. 环境对接

- 明确当前本地 qlib 数据/任务配置：
  - 数据源：`~/.qlib/qlib_data/cn_data`，日频、Alpha158 等；
  - 典型任务：LightGBM + Topk/LiveTopk（workflow_config_lightgbm_Alpha158_2020_2025.yaml）。
- 调研 RD-Agent-Quant 仓库：
  - 搭建最小可运行 demo（因子挖掘 / 模型优化）；
  - 确认其对 qlib 的依赖版本与本地环境兼容。

## 2. 任务抽象与接口设计

- 将当前常用的 qlib 任务抽象成 RD-Agent 可调用的“原子组件”：
  - 数据准备：给定股票池、时间区间、因子配置，输出特征矩阵；
  - 模型训练：给定模型配置（如 LightGBM 参数），输出训练好的模型与预测结果；
  - 策略回测：给定预测结果与策略配置（Topk/LiveTopk/LowDD 版本），输出收益/回撤指标。
- 设计统一接口：
  - 输入：一组候选因子定义 + 模型参数 + 策略参数；
  - 输出：关键评估指标（年化、回撤、夏普、信息比等）+ artefacts 路径。

## 3. 因子挖掘循环集成

- 利用 RD-Agent-Quant 的因子挖掘能力：
  - 从预定义因子空间（包括 Alpha158 变体、自定义价量因子、新闻/研报因子等）中自动采样/组合；
  - 用 qlib 的数据管道计算因子值；
  - 通过统一接口训练模型 + 回测策略，记录表现。
- 为个人场景定制：
  - 增加“回撤约束”的过滤逻辑（例如只保留最大回撤 < 某阈值的因子组合）；
  - 将你关心的 LowDD 策略作为默认策略模板参与自动评估。

## 4. 模型/策略联合调参环集成

- 模型层：
  - 针对给定因子集，使用 RD-Agent-Quant 自动搜索 LightGBM / 其它模型的超参数；
  - 引入早停/资源限制，避免搜索空间过大。
- 策略层：
  - 在找到较优模型后，对策略参数（topk、risk_degree、止损比例、max_stock_weight 等）进行二次搜索；
  - 尤其关注“收益-回撤权衡最优”的参数组合。

## 5. 实验管理与结果可视化

- 利用 qlib + mlflow：
  - 为每次 RD-Agent 触发的实验记录：因子集合、模型参数、策略参数、关键指标；
  - 提供汇总表或可视化（如 top-N 实验的收益-回撤散点图）。
- 为后续手工深入研究预留入口：
  - 能从 RD-Agent 输出中快速回溯到某一次配置，单独用 qrun 复现并细看交易细节。

## 6. 实施顺序建议

1. 先在一个简单子集（如 csi300、短时间窗口）上跑通 RD-Agent-Quant 对接 qlib 的完整链路（因子 → 模型 → 策略 → 回测）。
2. 再将 LowDD 风控策略接入 RD-Agent 的评估环，让自动搜索时直接以“低回撤策略版本”为评估对象。
3. 最后扩展到更长时间、更大股票池，并引入更多自定义因子源（如研报/新闻等），形成一个适合个人使用的“半自动策略研发工厂”。


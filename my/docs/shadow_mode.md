# 影子模式运行说明

**状态**：🚧 共享执行规划器、策略发布包和379日历史对账已通过；首个正式季度模型尚未训练/发布，暂不启用每日正式调度。

## 固定策略口径

- 资金：100,000 元，满仓口径
- 信号：Alpha158 + LightGBM，5日标签，季度滚动模型
- 组合：TopK=50、每日最多替换2只、开盘执行
- 门控：SH000905（中证500）MA20，连续3日线下离场，单日上涨超过2.5%强制回场
- 成本：买入0.05%、卖出0.15%、最低5元、单边滑点0.1%

以上口径现在统一来自 `my/strategies/lgb_alpha158_gate905_v1/`：

- `workflow.yaml`：Alpha158、5日标签和LightGBM参数
- `rolling.yaml`：季度滚动窗口，训练 `[q-42m,q-6m)`、验证 `[q-6m,q-7d)`
- `strategy.yaml`：门控、TopK/Drop2、成本和执行参数
- `models/` + `releases/`：经回测批准的原生模型、特征顺序和哈希清单，全部进入Git

`my/quant/config.py` 只是兼容门面，影子、Qlib适配器和未来QMT从同一策略包读取参数。日常影子不会自动训练：缺模型、发布清单、验证报告，模型/配置/特征哈希不一致，或正式文件未提交Git/存在本地修改时，都会在写信号和状态前硬失败。

## 命令

所有命令都从仓库根目录执行，并使用项目虚拟环境：

```bash
# 先进入Qlib Git仓库根目录

# 查看正式影子账户状态
.venv/bin/python my/scripts/shadow_run.py status

# 正式模型回填；区间涉及的每个季度都必须已经发布
.venv/bin/python my/scripts/shadow_run.py backfill 2026-07-20 2026-08-01

# 每晚正式运行：更新数据、结算昨日订单、记净值、生成次日订单
.venv/bin/python my/scripts/shadow_run.py nightly

# 用归档预测对比影子回放与 Qlib 普通回测（默认1.5年）
.venv/bin/python my/scripts/compare_shadow_backtest.py

# 额外生成双方价格冲击都为0的严格成本控制报告
.venv/bin/python my/scripts/compare_shadow_backtest.py --strict-cost-control

# 仅在允许的重训时段显式训练候选；产物仍在my/artifacts，不会自动发布
.venv/bin/python -m my.strategies.lgb_alpha158_gate905_v1.workflow train-candidate 2026-08-14

# 候选必须与已验收归档评分逐值一致，并且每日Top100完全重合
.venv/bin/python -m my.strategies.lgb_alpha158_gate905_v1.workflow compare-candidate 2026-08-14 \
  --archive my/artifacts/candidate1_pred.pkl

# 先人工评审并写入releases/2026Q3-validation.md，再生成待提交的正式模型和清单
.venv/bin/python -m my.strategies.lgb_alpha158_gate905_v1.workflow promote-candidate 2026-08-14

# 发布后校验某个信号日应使用的季度模型
.venv/bin/python -m my.strategies.lgb_alpha158_gate905_v1.workflow verify 2026-08-14
```

工作日重训只在 23:00 后启动。候选必须先与已验收归档评分完成索引、逐值和每日Top100校验，再生成验证报告；`promote-candidate` 只提升通过校验的候选。回填账本写入 `my/quant_state/backfills/`，正式账本写入 `my/quant_state/`；两者不得混用，整个账本目录不会提交 Git。

`compare_shadow_backtest.py` 是受控的历史对账工具：它显式注入 `candidate1_pred.pkl` 归档评分并绕过正式模型加载，只用于验证交易逻辑；普通 `prepare/nightly/backfill` 不享受这个例外。

## 每日产物

- `state.json`：现金、持仓、最近价格、最后处理日和待执行日
- `signals/YYYY-MM-DD.json`：T-1锁定的门控、全评分和Top100候选，并记录策略ID、季度发布号、模型/配置/运行代码哈希和来源Git提交
- `orders/YYYY-MM-DD_sell.csv` / `_buy.csv`：T日分阶段规划订单
- `receipts/YYYY-MM-DD_sell.csv` / `_buy.csv`：影子分阶段成交结果
- `skips/YYYY-MM-DD_sell.csv` / `_buy.csv`：不可交易、已持有等跳过原因
- `nav.csv`：每日净值、现金、持仓数和门控状态
- `shadow.log`：数据过期停摆等运行事件

数据未覆盖应处理信号日时必须停摆，不允许沿用旧数据出新单。相同或更早的信号日重复运行会幂等跳过。

## 当前验证结果

2026-08-04 使用归档同口径预测和真实行情完成 2026-07-20~07-28 隔离烟测：

- 7个交易日，净值日期无重复
- 一次16只建仓和次日16只清仓，共32笔回执，全部成交
- 最低现金75,191.30元，无负现金、超卖或残仓
- 终值100,110.27元
- 影子核心和策略发布校验测试通过

这轮只验证执行和账本链路，不替代季度模型实时打分版的完整回填。

2026-08-05 完成共享规划器版本的 2025-01-02~2026-07-28 共379个交易日对账：

- 门控282个在场日/97个离场日完全一致
- Qlib、影子和未来 QMT 共用 T-1 Top100、执行日过滤补选、先卖后买、实际卖后现金重算股数和0.3%限价保护
- 严格同成本控制下，订单、持仓和整日匹配率均为100%，首次订单/持仓分叉为空，规划订单差异为0
- 严格控制下逻辑账户现金最大只差0.05元；Qlib期末138,072.07，影子期末135,343.41，剩余净值差来自Qlib复权估值与影子原始价/停牌缓存估值，不改变交易队列
- 生产成本语义下，Qlib期末138,069.13，影子期末128,492.62，订单匹配率80.08%、持仓匹配率83.19%；差异来自Qlib成交量相关 `impact_cost` 与影子固定0.1%价格滑点经过整手门槛后的路径放大
- 修复了Qlib清仓复权碎股、每日factor漂移反推股数，以及前收缺失时影子错误判定不可交易的问题
- 最终报告见 `my/artifacts/shadow_backtest_parity/shared-planner-v3-20250102-20260728/report.md`，严格控制子报告见其 `strict_cost_control/report.md`

2026-08-14 策略发布包迁移后再次运行相同379日验收，结果未变化：生产语义仍为 Qlib 138,069.13 / 影子 128,492.62；严格控制仍为 Qlib 138,072.07 / 影子 135,343.41，订单与持仓100%一致、首次分叉为空、规划差异0、现金最大绝对差0.0476元。最终复验报告位于 `my/artifacts/shadow_backtest_parity/strategy-package-final-20260814/`；全量 `my/tests` 为118项通过。

## 正式启用前检查

1. 对ST/退市/长停票加入组前过滤和账户告警。
2. 23:00 后显式训练2026Q3候选，按固定Workflow重新回测；通过后发布到策略包的 `models/` 和 `releases/` 并提交Git。
3. 用正式发布模型完整回填 2026-07-20~08-01，核对订单、回执、状态、净值和异常日志。
4. 完整回填通过后，再配置 20:30~次日08:00 的多轮调度与告警。
5. QMT 试用环境可用后，按标准QMT设计复用同一策略包和规划器，先跑模拟/试用，不直接连接实盘资金。

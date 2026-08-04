# Codex 交接文档

最后更新：2026-08-04（Claude Code 编写给 Codex 接手用）

---

## 一、项目概览

这是一个基于 Microsoft **Qlib** 框架开发的 A 股日频量化投资系统，目标是从纯研究走向实盘。当前阶段：**影子模式核心、真实行情局部回填和379日历史对账已通过，完整季度模型回填待 23:00 后执行**。对账已实证Qlib回测的执行日可交易性/补选假设明显乐观，QMT前必须拍板实时重算与备选机制。

### 用户画像
- 周凡，在 Mac 上开发（macOS，Homebrew Python，仓库在 `/Users/bytedance/code/qlib`）
- 虚拟环境：**仓库根 `.venv`**（Python 3.9.6，不要用系统/conda 环境）
- 数据在 `my/data/cn_data/`（来自 chenditc/investment_data GitHub Release 打包的 Qlib 二进制数据）
- 偏好：**绝对回撤低、本金不缩水**，账户回撤上限 -10%（稍超可接受）；核心仓在支付宝自理，量化账户满仓跑原版策略（不内部打折），10 万起步
- 券商：国信 iQuant（已弃用，嫌不稳定）→ 国金**标准 QMT**（正在开户中，MiniQMT 不可开）；QMT 部署在家中 Windows 电脑
- 文件桥 Mac↔Windows：Syncthing（先行）/坚果云（兜底）/git（仅留痕）
- **绝对不买 Tushare**（影子模式阶段），实盘前必配免费指数备源
- 工作日白天用 Mac 办公，重实验排 23:00 后

---

## 二、目录结构

```
/Users/bytedance/code/qlib/              ← git 仓库（.venv、.claude 都在这层）
├── qlib/                               # 微软框架包（冻结区，研究/影子只调用不改）
├── my/                                 # ★ 个人工作区
│   ├── quant/                          # 新建的可复用核心包（影子模式核心，本轮新建）
│   │   ├── __init__.py
│   │   ├── config.py                   # 全局口径/路径/超参
│   │   ├── data.py                     # qlib 初始化、数据更新+硬校验、日历/行情/指数备源
│   │   ├── gate.py                     # 趋势门控 V2（MA20+3天确认+2.5%暴涨回场）
│   │   ├── signal_.py                  # 季度滚动 LGB 模型训练/加载 + 每日打分
│   │   ├── portfolio.py                # 单步 TopkDropout 决策（Order、decide）
│   │   ├── execution.py                # 执行器接口 + ShadowExecutor（将来加 QMTExecutor）
│   │   ├── ledger.py                   # 账本持久化（state.json/orders/receipts/nav/shadow.log）
│   │   ├── nightly.py                  # 每晚流程编排 run_evening()
│   │   └── parity.py                   # 影子回放 vs Qlib 逐日对账
│   ├── scripts/
│   │   ├── shadow_run.py               # 影子模式入口（nightly/backfill/status 三命令）
│   │   ├── compare_shadow_backtest.py  # 379日对账CLI+报告输出
│   │   ├── candidate1_rolling.py       # 滚动回测参考实现（季度重训范式）
│   │   ├── exp_gated_100k.py           # 10万门控引擎版（GatedTopkDropout 类）
│   │   ├── exp_gate_thermometer.py     # 门控温度计替换沙盘（985/905/852/自建对比）
│   │   ├── exp_gate905.py              # 905 温度计邻域稳健性+引擎级复验（刚写，还没跑）
│   │   ├── exp_longwindow_*.py         # 长窗回测族（各种）
│   │   ├── exp_mlflow_log.py           # 实验落档 mlflow
│   │   ├── package_dashboard.py        # 统一打包 faux recorder 仪表板（禁内联）
│   │   ├── recorder_visualizer_from_path.py  # 用户的 HTML 仪表板渲染器
│   │   ├── update_research_data.py     # 数据更新硬校验脚本
│   │   └── ...其他实验脚本
│   ├── configs/                        # qrun 实验 yaml
│   ├── trading/                        # 旧实盘线脚本（manual_daily_trade.py、iquant_qlib.py、data_update_guard.py 等）
│   ├── artifacts/                      # 实验产物（pkl/html/faux_recorders/，gitignore）
│   ├── mlruns/                         # MLflow 实验记录（gitignore）
│   ├── data/cn_data/                   # Qlib 二进制数据（gitignore，日历最新 2026-08-03）
│   ├── docs/
│   │   ├── research_log.md             # 研究台账（排行榜/想法队列/实验流水/烧窗口）
│   │   ├── features.md                 # 功能文档
│   │   └── specs/                      # 设计文档
│   └── quant_state/                    # 影子/实盘账本（运行时生成，gitignore）
│       ├── state.json
│       ├── models/                     # 季度模型 txt 文件
│       ├── orders/{date}.csv
│       ├── receipts/{date}.csv
│       ├── nav.csv
│       └── shadow.log
├── examples/                           # 微软官方示例
├── docs/                               # 官方文档
├── scripts/                            # 官方数据下载脚本
├── .venv/                              # Python 虚拟环境（Python 3.9.6，所有 python/qrun 命令用它）
├── AGENTS.md / CLAUDE.md -> AGENTS.md  # 项目说明（CLAUDE.md 是软链接）
├── HANDOVER.md                         # 本文件
├── PROJECT_MEMORY.md                   # 项目记忆（权威状态记录，每次进展更新）
└── Makefile / pyproject.toml / setup.py
```

---

## 三、策略研究状态（截至 2026-08-03）

### 当前榜首候选策略（决赛候选）

**配方**：开盘执行（deal_price=open）+ n_drop=2 + hold_thresh=1 + only_tradable=True + 5日label（`Ref($close,-6)/Ref($close,-1)-1`）+ 季度滚动重训（train 42月、valid 6月、gap 7天）+ 门控V2 + impact_cost=0.001

**成本口径**：open_cost=0.0005, close_cost=0.0015, min_cost=5, impact_cost=0.001, limit_threshold=0.095

**成绩（长窗 2021-2026，100万口径）**：
- 净超额年化 +19.4%，IR 1.00
- 绝对年化 +17.7%，绝对回撤 -37.2%
- 2021-22 干净段净 +29.5%（窗口拟合嫌疑洗清）
- 门控V2（MA20+3天确认+2.5%暴涨回场）后 10万口径：年化 +18.2%，回撤 -24.1%，净超额 +11.8%，IR 0.64

**距验收线差**：
- 净超额 >10% ✅ 已过
- IR >1.5 ❌ 当前 0.64~1.00，差距大
- 至多 1 个负年 ❌ 长窗有 2 个负年
- 成本翻倍保留 >60% ✅
- 参数敏感性 ✅（甜区 n_drop 2-3）
- 5 万复测 ❌（策略需 ≥10 万生效，5 万死亡螺旋）

**IR 病理**：收益集中最好 20 天贡献超额 181%；超额单次回吐 -30.6%；集成证伪（平均磨掉尖端=磨掉 alpha，IR 0.72→0.51）→ IR 病根=策略经济本性非模型方差。

**研究队列仅剩**：#12 风格约束（单行业/风格暴露上限）——这是 IR 的最后一张牌。如果再败，IR 1.5 适用性上升为方向级拍板（用户需决定是否接受 IR <1.5，或接受更低收益换稳定）。

### 排行榜（详见 `my/docs/research_log.md` 顶部）

1. 开盘+n_drop2+5日label+滚动+门控V2 — 净+13.4%/IR0.72（决赛候选）
2. 过滤入组 — 净-2.9%
3. csi300 — 净-3.1%
4. all_no_bj(收盘) — 净-3.9%
5. csi500 — 净-11.2%
6. 基准：躺平买 300ETF — 0

### 烧窗口纪律（极重要）
- **2025-08~2026-07 重烧**：死因诊断轮反复迭代，不得报最终成绩
- **2023-01~2025-07 轻烧**：票池对比轮已用，可做关卡淘汰；最终验收靠长窗(2021起)+影子模式
- 沙盘（收益率缩放）系统性高估门控效果（如门控V2沙盘承诺IR1.0实际只到0.64；沙盘高估6pp），**沙盘只筛方向，幅度必须以引擎级回测为准**
- 一次一变量铁律；任何工具调用**不传 model 参数**（ccs 环境）

---

## 四、影子模式核心包 `my/quant/` 详细说明

### 4.1 `config.py` — 全局配置
集中所有可调参数。当前门控温度计已固定为 `GATE_INDEX = "SH000905"`（中证500）：
- 原因：上游数据包的 SH000985 中证全指从 2026-07-06 起断更
- `exp_gate905.py` 已完成邻域稳健性和10万引擎级复验：净超额 12.52%、IR 0.68、绝对回撤 -21.7%，优于旧985版
- `gate.py` 的说明文字和 `data.index_close_fallback()` 默认指数均已跟随配置
- 其他参数（TOPK=50, N_DROP=2, GATE_MA=20, GATE_CONFIRM_DAYS=3, GATE_SURGE_REENTRY=0.025）是定版值，不要随便改

### 4.2 `data.py` — 数据层
- `init_qlib(kernels=4)` — 幂等初始化 qlib（全局 _qlib_ready 标志）
- `update_data()` — 调用 `update_research_data.py`（四层硬校验：manifest前置→sha256→日历硬闸→复查），**更新后会自动 `_rebuild_pool_file()`**（数据包整目录替换会抹掉 `all_no_bj.txt`，这是修过的 bug）
- `calendar()` / `future_calendar()` — 读 calendars/day.txt 和 day_future.txt
- `latest_data_date()` — 本地数据最后一天
- `next_trade_date(date)` — 用 day_future 判断下一交易日
- `day_bars(date, fields)` — 单日全票池行情，返回 DataFrame(index=instrument, columns=[open/close/volume/factor/prev_close])；价格是 qlib 复权价，需要除以 factor 还原原始价
- `index_closes(index_code, start, end)` — 指数收盘序列
- `index_close_fallback(index_code)` — 腾讯免费备源（qt.gtimg.cn），默认跟随 `C.GATE_INDEX`；数据包断供时救门控

### 4.3 `gate.py` — 门控 V2
- `gate_series(closes)` — 输入指数收盘序列，输出逐日布尔 Series（True=在场）
  - 逻辑：连续 GATE_CONFIRM_DAYS=3 天收于 MA20 下方 → 离场；单日涨幅 > 2.5% → 强制回场（覆盖离场信号）
- `gate_for_next_day(asof)` — 用截至 asof 的指数数据判定次日状态，返回 `(bool, note_str)`
- note 中的指数名称跟随 `C.GATE_INDEX_NAME`，当前为“中证500”。

### 4.4 `signal_.py` — 信号层
- `quarter_start(date)` / `model_path(q)` — 季度模型路径 `STATE_DIR/models/{Y}Q{q}.txt`
- `ensure_model(for_date)` — 确保当季模型存在，缺则训练
  - 训练窗口：train = [q-42月, q-6月), valid = [q-6月, q-7天)
  - 用 Alpha158 特征 + LGB_PARAMS（与研究流水线同参）
  - 早停 EARLY_STOP=100，best_iteration 保存
- `scores_for(date)` — 对 date 全票池打分，返回 Series(index=instrument, name="score")
  - 特征窗口 400 自然日（Alpha158 最长回看 60 交易日留余量）
  - 用 DK_I（推断集），单 datetime 切片

### 4.5 `portfolio.py` — 组合决策
- `Order` dataclass: `(code, side, shares, ref_price, reason)`
- `_lot_round(shares)` — 整手取整（LOT=100）
- `decide(scores, holdings, cash, ref_prices, gate_on)` — 单步 TopkDropout：
  - `gate_on=False` → 全清仓单（gate_off_liquidate）
  - 否则：持仓按分数排序 last，未持仓高分池 today = not_held[: N_DROP + max(TOPK-len(held), 0)]
  - 合并 last+today 排序取倒数 N_DROP 做卖出
  - 买入预算 = (cash + 卖出预估收入) × RISK_DEGREE(0.95) / 买入数
  - 按 ref_price（信号日收盘，原始价）算股数、整手取整
- **注意**：这里的 ref_prices 是未复权收盘价（已除 factor），与 day_bars 返回的复权价不同

### 4.6 `execution.py` — 执行层
- `Receipt` dataclass: `(code, side, shares, price, cost, status)` — status 为 filled/blocked_limit/suspended/no_data
- `Executor` Protocol — 只有 `settle(orders, exec_date) -> List[Receipt]`
- `ShadowExecutor.settle()` — 影子成交：
  - 读 exec_date 日 day_bars（open/prev_close/volume/factor）
  - 停牌（vol≤0 或 NaN）→ suspended
  - 买：开盘涨幅 >9.5% → blocked_limit；否则成交价 = raw_open × (1+IMPACT_COST)，cost = max(shares×price×OPEN_COST, MIN_COST)
  - 卖：开盘跌幅 < -9.5% → blocked_limit；否则成交价 = raw_open × (1-IMPACT_COST)，cost = max(shares×price×CLOSE_COST, MIN_COST)
  - raw_open = open_adj / factor（复权价还原原始价）

### 4.7 `ledger.py` — 账本持久化
目录布局：
- `state.json` — {cash, holdings: {code: shares}, last_settled, pending_exec_date}
- `orders/{date}.csv` — 订单文件（code,side,shares,ref_price,reason）
- `receipts/{date}.csv` — 成交回执（code,side,shares,price,cost,status）
- `nav.csv` — 净值流水（date,nav,cash,n_holdings,gate_on,note）
- `shadow.log` — 文本日志（带时间戳）

函数：`load_state/save_state/save_orders/load_orders/save_receipts/append_nav/append_log`

### 4.8 `nightly.py` — 每晚编排
`run_evening(asof=None, skip_update=False, log=print)` 流程：
1. **数据更新**（skip_update=True 跳，用于回填）；失败沿用本地
2. **确定 today**：asof 或数据最新日
3. **停摆检测**：latest_data_date < today → 记 STALL 日志，不出单，返回
4. **结算昨日挂单**：pending_date ≤ today 时，用 ShadowExecutor 结算 pending_date 的订单
   - 成交则更新 holdings/cash
   - 卖出被涨跌停/停牌拦截 → 入 retry 列表，挂到明天
5. **Mark-to-market 净值**：按今日收盘价计算（停牌股沿用最近价缓存 `holdings_price_cache`）
6. **门控判定**：gate_for_next_day(today) → 明日状态
7. **生成明日订单**：读今日收盘价（还原原始价），gate_on=True 则打分解 portfolio.decide；gate_on=False 则清仓单
8. **追加 retry 卖单**到明日订单
9. **存档**：save_orders(next_day)、save_state、append_nav
10. 返回 summary dict

**已知 bug**（nightly.py 第 35 行）：`holdings_price_cache` 在函数外定义为模块级全局变量，但函数内第 35 行直接赋值 `holdings_price_cache[code] = raw`——在函数内给全局 dict 的 key 赋值是可以的（不会报 UnboundLocalError），但回填模式跨日时缓存会累积，**可能需要在 backfill 初始时清空**。

### 4.9 `shadow_run.py` — 入口（薄壳）
```bash
# 每晚正式跑
cd /Users/bytedance/code/qlib && .venv/bin/python my/scripts/shadow_run.py nightly

# 历史回填验证 [A,B] 区间（不更新数据）
.venv/bin/python my/scripts/shadow_run.py backfill 2026-07-20 2026-08-01

# 查看状态
.venv/bin/python my/scripts/shadow_run.py status
```

---

## 五、当前进行到哪（Task #8 影子模式端到端验证）

### 已完成
- ✅ my/quant/ 八模块包全部写好，py_compile 通过
- ✅ shadow_run.py 入口可用
- ✅ .gitignore 加了 my/quant_state/
- ✅ 中证500门控邻域和引擎级复验通过，正式配置已切换 SH000905
- ✅ 数据层 bug 修复：
  - SH000985 中证全指断更问题定位（7-06 起）
  - update_data 整目录替换抹掉 all_no_bj.txt → 加 `_rebuild_pool_file()` 自动重建
- ✅ 影子核心补齐旧数据停摆、同日/倒序幂等、资金不足、超卖、卖单重试去重、停牌最近价持久化和回填账本隔离
- ✅ `my/tests/test_shadow_core.py` + `my/tests/test_shadow_parity.py` 共45 项通过（含合成两日闭环、factor 微漂移保护、差异分类、T-1 输入硬校验和 Qlib 适配器注入测试）
- ✅ 用归档同口径预测 + 真实行情完成 2026-07-20~07-28 隔离回填烟测：7个交易日、一次16只建仓和次日清仓、32笔回执全部 filled、无负现金/残仓/重复净值，终值 100,110.27 元
- ✅ 新增 `parity.py` 和 `compare_shadow_backtest.py`，完成影子回放、Qlib引擎录制和五份对账产物
- ✅ 2025-01-02~2026-07-28 共379个交易日完整对账：生产语义 Qlib期末144,631.46（+44.63%，MDD -16.29%），影子121,248.55（+21.25%，MDD -24.22%）；v5 分类为 rounding_or_cost=505、execution_tradability=556、selection_or_path_dependency=1382，报告 `my/artifacts/shadow_backtest_parity/full-20250102-20260728-v5/report.md`
- ✅ 同一输入的严格成本控制（双方价格冲击=0）期末 Qlib 144,630.01、影子126,959.83；成本语义只解释约5,711元差异，其余主要是执行/路径差异
- ✅ 首次分叉定位到2025-01-15：影子有6笔涨停禁买，Qlib `only_tradable=True` 可过滤并补选；明确成交性回执事件共556条（79只股票、217个交易日），其余单边差异保守标成路径依赖

### 还没做（Codex 接手的第一优先级）
1. **先拍板执行语义**：建议影子/QMT前夜输出排名候选，执行日用实时行情过滤、按当日价重算股数并补选；否则不得以Qlib回测绩效作为实盘预期
2. **加停牌/退市风险防护**：候选池排除ST/退市/长停票，对账期期末有5只异常持仓未能正常退出
3. **23:00 后训练 2026Q3 模型**：直接跑 backfill 会自动触发并保存到 `my/quant_state/models/2026Q3.txt`
4. **完整真实模型回填验证**：选 2026-07-20 ~ 2026-08-01 跑
   ```bash
   .venv/bin/python my/scripts/shadow_run.py backfill 2026-07-20 2026-08-01
   ```
5. 核对模型实时打分版的 orders/receipts/state/nav、涨跌停/停牌/重试日志及净值连续性
6. 完整回填通过后再启用每日调度；当前不要把局部烟测当成正式影子账户

### 回填验证中需要重点排查的潜在 bug
- **回测/实盘可交易性差异**：研究回测 `only_tradable=True` 可在执行日跳过不可交易候选；当前隔夜订单遇次日涨停/停牌只会拒单，不会自动换入备选。影子期需统计该差异，QMT 接入前决定是否输出备选候选并在开盘重算。
- **execution.py 未处理部分成交**：影子模式下要么全成交要么 0，与回测一致；QMT 版再处理
- **signal_.py _build_handler** 每次调用都新建 Alpha158（会重新加载特征），backfill 连续跑多天时每天建一次 handler 很慢——可优化为缓存，但正确性优先
- **gate.py gate_for_next_day** 在数据不足时默认返回 True（在场），首次回填 start 日期太早时可能误判——回填从 7-20 开始，start 倒推 80 天到 4 月，数据充足，问题不大
- **ledger 无原子写入**：写 state.json/CSV 过程中崩溃可能损坏文件（影子阶段可接受，实盘前需加 tmp+rename）
- **shadow_run.py 硬编码绝对路径** `/Users/bytedance/code/qlib`，换机器要改

### Task #9（回填通过后做）
- 配 crontab：每晚 20:30 起多轮重试至次日 8:00（数据包中位发布 19:11，0/100 晚于次日 8:30）
- 按 `my/docs/shadow_mode.md` 启用正式影子账本并观察 2~4 周
- git 提交
- 同步配置 Syncthing（Mac↔Windows）——这项是用户的系统配置，不写入代码

---

## 六、关键踩坑教训（必读）

1. **stdin heredoc + qlib kernels=4 多进程 spawn 崩溃**：写长脚本必须落盘成 .py 文件 + `if __name__=="__main__"` 保护；FileNotFoundError '<stdin>' 常被 grep 过滤导致 10 分钟超时假象
2. **沙盘（收益率缩放）系统性高估门控效果**，幅度必须以引擎级回测为准（已有两次实锤）
3. **pandas reindex 多级索引 bug**：跨 (datetime,instrument) vs (instrument,datetime) 会全 NaN，用 join 按名对齐
4. **plotly 版本不匹配**：CDN 用 plotly-latest(1.x) 解不了 plotly 6.x 的 bdata 序列化会渲染成直线，CDN 锁 plotly-3.7.0.min.js
5. **集成实验污染**：误加 bagging_freq=1 等于同时改两变量，结果作废；**一次一变量**铁律
6. **risk_degree 不是仓位控制**：只是每日买入预算节流阀，仓位最终仍会爬满；物理隔离法才是真仓位控制
7. **TopkDropout 死票锁死**：only_tradable=True 必须开（否则持仓膨胀后垫底死票不可交易→卖0买0永久冻结）
8. **iQuant/QMT 必须 is_last_bar() 检查**：历史回放阶段 passorder 会静默返回 0 不进系统
9. **ccs 环境下任何工具调用不传 model 参数**：传了不生效还可能路由到弱模型
10. **后台长任务监控关键词必含通用 ERROR|Error|Traceback**：只列具体异常名会漏掉未知崩溃白等
11. **all_no_bj.txt 被数据包更新抹掉**：每次 update_data 后必须 _rebuild_pool_file
12. **min_cost=5 元地板对小账户=6.1pp/年杀伤**：5 万级别策略净被吃光；10 万是最低生效资金
13. **alpha 集中在尖端**：集成平均会磨掉最好 20 天贡献的 181% 超额而不是降低方差
14. **数据包缺发率 ~15/83 工作日**：影子模式必须有停摆机制+日志统计，不能静默用旧数据

---

## 七、常用命令速查

```bash
# 激活环境（不需要 activate，直接用绝对路径）
PY=/Users/bytedance/code/qlib/.venv/bin/python
QRUN=/Users/bytedance/code/qlib/.venv/bin/qrun
cd /Users/bytedance/code/qlib

# 跑 qrun 实验（cwd 必须在 my/ 下避免包名遮蔽）
cd my && ../.venv/bin/qrun configs/xxx.yaml

# 跑实验脚本
.venv/bin/python my/scripts/exp_xxx.py

# 影子模式
.venv/bin/python my/scripts/shadow_run.py status
.venv/bin/python my/scripts/shadow_run.py backfill 2026-07-20 2026-08-01
.venv/bin/python my/scripts/shadow_run.py nightly

# 更新数据（手动）
.venv/bin/python my/scripts/update_research_data.py

# 打包仪表板（统一入口，禁止内联）
.venv/bin/python my/scripts/package_dashboard.py <实验名> <pred.pkl> <report.pkl>

# 代码检查
.venv/bin/python -m black my/ -l 120
.venv/bin/flake8 --ignore=E501,F541,E266,E402,W503,E731,E203 my/quant/ my/scripts/shadow_run.py

# py_compile 快速语法检查
.venv/bin/python -m py_compile my/quant/*.py my/scripts/shadow_run.py
```

---

## 八、冻结区 / 施工区规则（strategy-research skill 约束）

- **冻结区**（只准调用，禁止修改）：
  - `qlib/` 框架本身
  - `my/trading/` 旧实盘线
  - 已有 `my/scripts/` 脚本（可以新增，不要改旧的）
  - 实验 pkl 产物作为历史记录不要删
- **施工区**：
  - `my/quant/` 影子模式核心包（当前正在调试，可以改）
  - `my/scripts/` 新增实验/验证脚本
  - `my/configs/` 新 yaml
  - `my/artifacts/` 新产物
  - `my/docs/` 台账和文档
- **git 纪律**：不要一实验一提交；阶段性节点汇总。当前工作区有未提交改动：
  - `.gitignore`、`PROJECT_MEMORY.md`、`my/docs/research_log.md`（修改中）
  - `my/quant/` 整个目录（新文件，未提交）
  - `my/scripts/shadow_run.py`、`exp_gate_thermometer.py`、`exp_gate905.py`（新文件）

---

## 九、strategy-research Skill 位置

`.claude/skills/strategy-research/SKILL.md`——项目级研究员规程，Codex 可以读但不需要手动加载（Claude Code 用 `/strategy-research` 触发）。核心四层：
1. 想法从哪来（线索→qlib榜单→经典改进，自定义因子需立项）
2. 实验怎么跑（一律滚动回测、锁死口径、机器分时段、夜跑必配监控）
3. 三关淘汰（海选 IC≥0.03+RankIC≥0.03/分层单调/可成交头部>0 → 复赛毛>12% → 决赛净>10%/IR>1.5/至多1负年/成本翻倍留60%/敏感性/5万复测 → 影子模式需拍板）
4. 每实验四件产出：mlflow落档+faux_recorder仪表板+三行战报+台账更新

---

## 十、悬置拍板事项（需要等用户决定的）

1. **研究口径升级 impact_cost=0.001**：事实已采用于新实验，正式确认待用户
2. **IR 1.5 验收线**：如果 #12 风格约束也不能把 IR 拉到 1.5，需要用户决定是否降级标准或接受低 IR
3. **Tushare 200元/年**：影子阶段不买，实盘前必配免费指数备源；全量备源 Tushare 等用户批准
4. **国金开户/QMT 权限/免5佣金/资金到10万**——用户线下推进中

---

## 十一、数据源细节

- **主源**：https://github.com/chenditc/investment_data/releases/latest
  - 下载 `qlib_bin.tar.gz`，manifest 为 `qlib_bin.manifest.json`（含 target_trade_date、archive_sha256、archive_size_bytes）
  - 中位发布时间 19:11，0/100 晚于次日 8:30
  - 缺发率 ~15/83 工作日（7月曾连续5天断供）
- **票池**：`all_no_bj.txt`（从 `all.txt` 剔除 BJ 开头北交所股票），目前 5545 只
- **指数代码**：SH000300（沪深300，基准）、SH000905（中证500，新门控温度计）、SH000852（中证1000）、SH000985（中证全指，断更中）
- **免费备源**：腾讯行情 `https://qt.gtimg.cn/q=sh000905`（GBK 编码，`~` 分割，第 4 段是最新价）

---

## 十二、后续路线图（影子模式通过后）

1. **影子模式 2-4 周**：验流程不验收益，跑通每日自动出单→手动执行→回报录入→结算全链路
2. **QMT 迁移**：新增 `QMTExecutor` 实现 Executor 接口（同 ShadowExecutor 接口，实盘下单部分用 QMT 的 XtQuant API）；Mac↔Windows 用 Syncthing 传 orders/receipts CSV
3. **PTrade 终局**：券商托管，彻底无人值守（需确认国金是否允许外部通信）
4. **IR 提升**：打完 #12 风格约束牌后再决定方向

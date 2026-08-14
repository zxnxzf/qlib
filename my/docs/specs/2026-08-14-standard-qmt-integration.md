# 标准 QMT 接入设计

日期：2026-08-14
状态：设计定稿，尚未开发
适用范围：国金标准 QMT 内置 Python；不使用 MiniQMT、XtQuant 外部接口

## 1. 一句话说明

个人 Windows 上同时运行一套生产 Qlib 和标准 QMT：Qlib 在 T-1 晚上生成 `signal.json`，QMT 在 T 日 9:30 读取它、查询真实账户和实时行情、完成先卖后买，再把实际执行过程写成 `result.json`。公司 Mac 只做研究，定版代码通过 Git 发布到 Windows，不持续接收账户或成交数据。

```text
公司 Mac（研究）
Qlib 回测、策略实验、确认发布版本
                  │
                  │ Git：代码、配置、模型版本清单
                  │ Windows只拉取确认过的commit/tag
                  ▼
个人 Windows（生产）
生产 Qlib ──signal.json──> 标准 QMT 内置脚本
   │                             │
   │                             ├─ 查询真实现金/持仓/行情
   │                             ├─ Drop2、先卖后买、Top100补选
   │                             └─ 报单、等待、撤单、接收成交
   │                             │
   └──────读取归档 <──result.json┘

按需影子回放（独立账本）
QMT先独立运行几天或半个月 → 集中回放同一观察窗口 → 批量对账

影子模式不与QMT同步运行。QMT先独立运行，观察窗口结束后再在Windows上集中回放；影子回放不阻塞QMT，也不改写QMT账户和结果。
```

文档中的“QMT → Qlib”是个人 Windows 内部的本地文件交互，不代表把券商数据传回公司 Mac。

### 1.1 Mac → Windows版本发布

- Git是Mac与Windows之间唯一的代码同步方式；不使用Syncthing、网盘或共享目录持续同步运行文件。
- Mac完成研究和验证后提交代码、配置及模型版本清单，并创建可识别的commit或tag。
- Windows生产环境只拉取明确确认过的commit或tag；拉取后先校验规划器版本、配置和模型版本，再生成下一交易日信号。
- 模型文件若因体积不进入Git，则通过单次人工复制部署，但其文件哈希和版本清单必须提交Git；运行时不得自动从公司Mac同步。
- `signal.json`、`result.json`、账户、持仓、行情快照和日志只在个人Windows本地流转，不进入Git。

## 2. 复用范围与隔离边界

### 2.1 直接复用

- GitHub 数据包更新和 T-1 数据新鲜度硬校验。
- Alpha158 + LightGBM 评分和季度模型。
- 中证500 MA20 门控：连续3日位于 MA20 下方则次日离场；单日上涨超过2.5%则次日强制回场。
- TopK=50、Top100候选、N_DROP=2、100股整手和资金预算规则。
- 共享规划器中的持仓低分卖出、不可交易过滤、卖后按实际现金补买和0.3%价格保护。
- 信号包的日期、批次、版本和校验码机制。

### 2.2 新开发

- Windows 生产 Qlib 的 QMT 信号生成入口。
- 标准 QMT 内置执行脚本。
- QMT API 适配：账户、持仓、行情、报单、查单、查成交和撤单。
- QMT 独立批次状态、执行结果归档和崩溃恢复。
- QMT 历史结果与共享规划器的只读对账。

### 2.3 必须隔离

- 影子模式使用仓库内现有的`my/quant_state`，QMT使用`my/runtime/qmt_state`，两边不得读写对方账本。
- QMT现金和持仓只认券商查询结果，不使用影子账本推算。
- QMT实际成交不得回写影子账户。
- 影子故障不能阻止QMT执行；QMT故障也不能破坏影子历史。
- Mac研究版本不能自动覆盖Windows生产版本；Windows只拉取人工确认过的Git commit或tag。
- 账户、资金、委托、成交和QMT日志只保留在个人Windows。
- 不建立Mac与Windows之间的运行目录双向同步；Git仓库中也不得提交任何账户或交易运行文件。

Windows不再单独创建`D:\qlib-prod`。直接把Windows上的Qlib Git仓库作为根目录，生产文件统一放在仓库的`my/`下。例如仓库克隆在`D:\code\qlib`时：

```text
D:\code\qlib\                 # Git仓库根目录，实际路径可不同
└── my\
    ├── data\                 # 生产Qlib数据
    ├── models\               # 已发布模型文件
    ├── quant_state\          # 影子模式现有独立账本
    └── runtime\
        ├── qmt_inbox\        # Qlib写、QMT读
        ├── qmt_outbox\       # QMT写、Qlib只读
        ├── qmt_state\        # QMT独立状态
        ├── qmt_performance\  # QMT真实绩效账本
        └── logs\             # QMT与生产Qlib运行日志
```

这些目录位于Git工作区内，但`my/data/`、`my/models/`、`my/quant_state/`和`my/runtime/`全部由`.gitignore`排除。Git只同步代码、配置和模型版本清单，不同步模型大文件、账户、订单、成交、绩效或日志。

## 3. Qlib → QMT：`signal.json`

### 3.1 生成时机

T-1 收盘数据发布后，Windows生产Qlib执行：

1. 检查GitHub manifest、压缩包校验值和Qlib交易日历末日。
2. 确认本地数据确实覆盖T-1；不满足则不出信号。
3. 计算MA20门控和模型评分。
4. 生成T日不可变信号包。
5. 先写 `signal.json.tmp`，刷新并关闭后再原子改名为 `signal.json`。

文件路径：

```text
my/runtime/qmt_inbox/<执行日>/signal.json
```

### 3.2 字段

| 字段 | 含义 |
|---|---|
| `schema_version` | 协议版本，第一版QMT协议固定为2 |
| `batch_id` | 唯一批次号，同一批次绝不重复执行 |
| `signal_date` | 模型和门控使用的数据日T-1 |
| `exec_date` | QMT执行日T |
| `created_at` | 信号生成时间，带Asia/Shanghai时区 |
| `expires_at` | 最晚有效时间，默认T日09:31 |
| `account_alias` | 本地配置中的模拟账户别名，不写真实账号 |
| `data_asof` | Qlib数据实际覆盖的最后交易日，必须等于`signal_date` |
| `model_version` | 季度模型版本，例如`2026Q3` |
| `planner_version` | 共享规划器版本，用于拒绝错误代码版本 |
| `gate` | MA20门控结果和可读说明 |
| `params` | TopK、Top100、Drop2、整手、成本预算和价格保护参数 |
| `scores` | 全股票池T-1评分，QMT用于给真实持仓排序 |
| `candidates` | 严格按分数排列的Top100买入候选 |
| `checksum` | 除自身外全部内容的SHA-256 |

保存全股票池评分而不是只保存影子持仓评分，是因为QMT的真实持仓可能与影子持仓不同。QMT不运行模型，只按 `scores` 查询实际持仓分数。

### 3.3 示例

```json
{
  "schema_version": 2,
  "batch_id": "2026-08-14_2026-08-17_2026Q3",
  "signal_date": "2026-08-14",
  "exec_date": "2026-08-17",
  "created_at": "2026-08-14T20:35:12+08:00",
  "expires_at": "2026-08-17T09:31:00+08:00",
  "account_alias": "qmt_sim",
  "data_asof": "2026-08-14",
  "model_version": "2026Q3",
  "planner_version": "shared-planner-v1",
  "gate": {
    "on": true,
    "index": "SH000905",
    "ma_window": 20,
    "confirm_days": 3,
    "surge_reentry": 0.025,
    "note": "中证500 站在MA20上方，次日在场"
  },
  "params": {
    "topk": 50,
    "candidate_limit": 100,
    "n_drop": 2,
    "hold_thresh": 1,
    "risk_degree": 0.95,
    "lot": 100,
    "open_cost": 0.0005,
    "close_cost": 0.0015,
    "min_cost": 5.0,
    "max_slippage": 0.003
  },
  "scores": {
    "SH600000": 0.013421,
    "SZ000001": -0.002187
  },
  "candidates": [
    {
      "rank": 1,
      "code": "SH600000",
      "score": 0.013421,
      "reference_close": 11.26
    }
  ],
  "checksum": "sha256:<计算结果>"
}
```

校验码使用UTF-8编码，对移除`checksum`后的对象按键名排序、无多余空格地序列化，再计算SHA-256。QMT发现版本、日期、账号别名、规划器版本或校验码不一致时，整批中止，不猜测修复。

## 4. QMT 内部执行逻辑

### 4.1 与现有 iQuant 接口的对应关系

现有 `my/trading/iquant_qlib.py` 是第一参考实现。国信iQuant和国金标准QMT属于同类内置策略环境，但国金版的字段和撤单函数必须实机确认。

| 职责 | iQuant已有接口/回调 | 新QMT脚本中的用途 |
|---|---|---|
| 策略初始化 | `init(ContextInfo)` | 设置账户别名、目录、内存状态 |
| 行情周期入口 | `handlebar(ContextInfo)` | 只在T日实时bar触发一次 |
| 排除历史预热 | `ContextInfo.is_last_bar()` | 非实时bar禁止报单 |
| 查询资金 | `get_trade_detail_data(..., "account")` | 获取可用现金 |
| 查询持仓 | `get_trade_detail_data(..., "position")` | 获取总持仓和可卖股数 |
| 实时盘口 | `ContextInfo.get_full_tick(codes)` | 转成共享规划器的行情快照 |
| 报单 | `passorder(...)` | 提交盘口保护限价单 |
| 委托回调 | `order_callback(...)` | 记录已报、部分成交、已成、已撤、拒单 |
| 成交回调 | `deal_callback(...)` | 汇总真实成交数量和均价 |
| 错误回调 | `orderError_callback(...)` | 记录拒单和接口错误 |
| 查委托/成交 | 国金QMT待能力探针确认 | 重启恢复和最终核对 |
| 撤单 | 国金QMT待能力探针确认 | 30秒撤销未成交余量 |

开发第一步必须在国金QMT模拟环境运行只读探针，打印Python版本和上述对象字段；随后用显式测试开关验证模拟报单、查单和撤单。没有确认真实接口前，不根据网上文档臆造函数名。

### 4.2 T日完整流程

```text
QMT启动
  ↓
init：读取本地配置，不报单
  ↓
handlebar：必须 is_last_bar() == True
  ↓
到达9:30后读取当日 signal.json
  ↓
校验日期、有效期、checksum、账户别名、规划器版本、batch_id
  ↓
查询券商真实现金、持仓和可卖数量
  ↓
读取“实际持仓 + Top100”所需实时盘口
  ↓
共享规划器：持仓按T-1评分从低到高，正常最多卖2只
  ↓
提交卖单 → 等待最多30秒 → 撤销未成交余量
  ↓
重新查询真实现金、持仓和可卖数量
  ↓
共享规划器：从Top100过滤不可买股票并补至最多50只
  ↓
按真实卖后现金计算100股整手买单
  ↓
提交买单 → 等待最多30秒 → 撤销未成交余量
  ↓
重新查询最终账户，写 result.json
```

具体规则：

- 正常门控：卖出实际持仓中T-1评分最低的最多2只；不可卖则按评分顺延下一只。
- 门控关闭：不按评分，尝试清仓全部可卖持仓，不生成买单。
- 实际持仓找不到T-1评分：不自动卖出，记录异常并报警；其余有评分持仓继续执行。
- 买入只从Top100按顺序补选；涨停、停牌、无有效盘口和买不起一手的股票跳过。
- 首次盘口定义价格保护区间：买入不高于`ask1 × 1.003`且不越过涨停价，卖出不低于`bid1 × 0.997`且不越过跌停价。
- 第一版不反复追价：提交一次保护限价单，最多等待30秒，未成交余量撤单并保留现金/持仓。
- 买单必须在卖单全部处于“已成、已撤或已拒”终态并重新查询账户后才能规划。
- `passorder`返回不等于成交成功；最终状态必须来自委托/成交回调或券商查询。
- 行情、账户或撤单接口异常时停止后续阶段，不使用旧行情或本地推算余额继续下单。

### 4.3 批次状态与防重复

QMT独立状态机：

```text
ready
  → sell_submitted
  → sell_closed
  → buy_submitted
  → completed / partial / aborted
```

- 报单前先把阶段和计划订单原子写入QMT本地状态。
- 每个订单ID固定为 `<batch_id>:<sell|buy>:<序号>`，同时写入QMT委托备注。
- `completed`、`partial`和`aborted`都是终态，同一批次再次触发只能读取结果，不能重复报单。
- 若程序在`sell_submitted`或`buy_submitted`阶段重启，必须先按订单备注查询券商委托和成交；无法确认时中止并要求人工检查，禁止重报。
- 信号在09:31前未到、已经过期或数据日不正确：当天不交易，写`aborted`结果。

## 5. QMT → Qlib：`result.json`

### 5.1 用途

`result.json`由QMT写到个人Windows本地，供生产Qlib归档和只读对账。它不更新影子账本，也不自动传给公司Mac。

文件路径：

```text
my/runtime/qmt_outbox/<执行日>/result.json
```

同样先写 `.tmp`，完成后原子替换正式文件。

### 5.2 字段

| 字段 | 含义 |
|---|---|
| `schema_version` | 结果协议版本 |
| `batch_id` | 对应信号批次号 |
| `signal_date` / `exec_date` | 信号日和执行日 |
| `planner_version` | 实际使用的规划器版本 |
| `started_at` / `finished_at` | 执行起止时间 |
| `status` | `completed`、`partial`或`aborted` |
| `reason` | 部分完成或中止的原因；完成时为空 |
| `account_before` | 执行前券商现金、持仓、可卖数量、证券市值和总资产 |
| `market_snapshot` | 规划时实际使用的盘口和涨跌停信息 |
| `sell_stage` | 卖出规划、跳过原因、委托、成交和撤单 |
| `account_after_sell` | 卖单终态后的券商现金、持仓、证券市值和总资产 |
| `buy_stage` | 买入规划、跳过原因、委托、成交和撤单 |
| `account_after` | 交易阶段结束时的券商现金、持仓、证券市值和总资产 |
| `errors` | 接口异常、拒单和人工检查事项 |
| `checksum` | 结果内容校验码 |

### 5.3 示例骨架

```json
{
  "schema_version": 1,
  "batch_id": "2026-08-14_2026-08-17_2026Q3",
  "signal_date": "2026-08-14",
  "exec_date": "2026-08-17",
  "planner_version": "shared-planner-v1",
  "started_at": "2026-08-17T09:30:02+08:00",
  "finished_at": "2026-08-17T09:31:04+08:00",
  "status": "partial",
  "reason": "one_buy_order_timed_out_and_cancelled",
  "account_before": {
    "cash": 5200.0,
    "market_value": 11250.0,
    "total_asset": 16450.0,
    "holdings": [
      {
        "code": "SH600000",
        "shares": 1000,
        "available_shares": 1000,
        "market_value": 11250.0
      }
    ]
  },
  "market_snapshot": {
    "SH600000": {
      "timestamp": "2026-08-17T09:30:03+08:00",
      "bid1": 11.24,
      "ask1": 11.25,
      "last": 11.25,
      "high_limit": 12.38,
      "low_limit": 10.13,
      "status": "normal"
    }
  },
  "sell_stage": {
    "planned": [],
    "skipped": [],
    "broker_orders": [],
    "fills": [],
    "cancelled": []
  },
  "account_after_sell": {
    "cash": 5200.0,
    "market_value": 0.0,
    "total_asset": 5200.0,
    "holdings": []
  },
  "buy_stage": {
    "planned": [],
    "skipped": [],
    "broker_orders": [],
    "fills": [],
    "cancelled": []
  },
  "account_after": {
    "cash": 5200.0,
    "market_value": 0.0,
    "total_asset": 5200.0,
    "holdings": []
  },
  "errors": [],
  "checksum": "sha256:<计算结果>"
}
```

`account_after`以券商查询为准。规划器根据成交回报计算的账户只用于一致性检查，二者不一致时报警并保留券商结果。该快照通常发生在9:31左右，只表示交易阶段结束状态，不能直接当作当日收盘净值。

### 5.4 收盘账户快照：`eod_snapshot.json`

为计算QMT真实收益，QMT在T日最后一个实时bar或收盘后再次查询券商账户，单独写入：

```text
my/runtime/qmt_outbox/<执行日>/eod_snapshot.json
```

至少包含：

| 字段 | 含义 |
|---|---|
| `schema_version` | 收盘快照协议版本 |
| `batch_id` / `exec_date` | 对应执行批次和交易日 |
| `snapshot_at` | 券商账户查询时间 |
| `cash` / `frozen_cash` | 可用及冻结资金 |
| `market_value` | 券商返回的证券总市值 |
| `total_asset` | 券商返回的账户总资产，作为净值事实来源 |
| `holdings` | 每只股票数量、可卖数量、收盘/最新价和市值 |
| `external_cash_flow` | 券商可查询时记录当日净入金减净出金；不可查询时为`null` |
| `source` | 固定为`broker_qmt`，禁止写成影子或本地推算值 |
| `checksum` | 快照内容校验码 |

缺少可靠`total_asset`时，当日绩效标记为`missing`并报警，不允许用影子账户、T-1价格或本地成交推算值冒充真实净值。能力探针必须确认国金QMT的总资产、市值、手续费字段及收盘后查询时机。

### 5.5 Windows Qlib侧QMT真实绩效账本

Windows生产Qlib读取`result.json`和`eod_snapshot.json`，生成与影子模式风格一致、但数据源完全独立的真实绩效账本：

```text
my/runtime/qmt_performance/
├── qmt_nav.csv             # 每日真实净值与收益指标
├── qmt_trades.csv          # 计划、委托、成交、费用与滑点
├── qmt_cash_flows.csv      # 券商无法返回时登记入金/出金
└── qmt_report.html         # QMT真实、影子回放和基准对比
```

`qmt_nav.csv`至少记录：交易日、批次号、现金、证券市值、总资产、外部现金流、日收益、累计收益、当前回撤、最大回撤、基准日收益、基准累计收益和累计超额收益。真实日收益按资金流调整：

```text
daily_return = (今日总资产 - 今日外部净流入) / 昨日总资产 - 1
```

`qmt_trades.csv`按`batch_id + order_id + fill_id`幂等记录计划价、委托价、真实成交价、成交数量、佣金/税费、撤单和相对计划价的滑点。券商没有提供的费用字段必须标记为未知，不得填模拟费率。

外部资金流优先使用券商字段；券商不提供时读取`qmt_cash_flows.csv`。第一版要求该账户只运行本策略且观察期内不随意转入转出；发生资金划转必须登记，未登记的异常资产跳变要报警。

复用影子模式的范围：

- 复用按日期幂等追加、收益率、累计收益、回撤、基准/超额和HTML展示代码。
- QMT与影子资金不同，比较图统一从各自首日归一化为1.0。
- 不复用影子的模拟账户、模拟成交、估值价格和`nav.csv`文件；QMT使用独立目录和文件名。
- 券商`total_asset`、真实成交及费用是唯一事实，Qlib只做统计和展示。
- 第一版要求模拟/实盘账户只运行这一套策略；发现无本系统`order_id`的人工委托或其他策略成交时，当日标记异常，避免把别的交易算成该策略收益。

## 6. 影子模式如何配合

影子模式不参与T日QMT下单，也不与QMT同步运行。推荐按“先观察、后集中回放”执行：

1. QMT在每个T日按实时行情独立执行，不等待影子模式。
2. 先积累一个观察窗口，初期可取5个交易日，稳定后可取10个交易日或半个月。
3. 窗口结束且对应GitHub日线数据齐全后，在Windows一次性回放整个区间。
4. 影子模式继续使用自己的10万元账户、模拟成交和独立账本；每次回放写入独立目录，不读写QMT状态。
5. 集中对账分成两类：
   - 规划对账：把QMT当天的真实账户和行情快照重新交给同版本共享规划器，计划订单必须完全一致。
   - 成交对账：影子假设成交与QMT真实成交允许不同，但必须能归因于价格、部分成交、撤单、拒单或账户约束。

影子与QMT长期持仓可能因真实成交差异逐渐分叉，这是正常现象；不要求两套独立账户每天持仓完全一样。

影子回放是阶段性验证工具，不配置正式每日调度。QMT每日只需保存完整`signal.json`、`result.json`和账户/行情快照，供之后批量重放。

## 7. 开发任务拆分

### 阶段A：QMT能力探针

- 基于 `iquant_qlib.py` 新建国金QMT探针脚本。
- 只读检查Python、文件、账户、持仓、总资产、市值、费用和行情接口。
- 模拟账户中显式开启一笔最小报单，验证委托回调、成交查询和撤单。
- 验证最后实时bar或收盘后仍能查询账户并写出`eod_snapshot.json`。
- 保存国金QMT真实对象字段，替代本设计中的待确认项。

验收：能够可靠查询账户/持仓/总资产/行情，并确认报单、查单、查成交、费用、撤单和收盘快照的准确接口。

### 阶段B：共享协议和规划器兼容

- 实现`signal.json` V2读写、校验和过期检查。
- 将共享规划器整理成国金QMT内置Python可直接导入的无Qlib/Pandas依赖模块。
- Mac/Windows Qlib与QMT对同一输入运行规划器，输出逐字段一致。
- 改造后重跑现有379日严格对账，保持订单和持仓100%一致。

### 阶段C：Windows生产Qlib信号入口

- 使用生产数据、模型和配置生成当日信号包。
- 缺数据、缺模型、日期错误和重复生成全部硬失败或幂等跳过。
- 与影子模式使用独立状态目录。

### 阶段D：QMT执行脚本

- 实现预检、卖单、等待/撤单、账户刷新、买单、结果归档、收盘账户快照和崩溃恢复。
- 先接假券商适配器跑自动测试，再接国金QMT模拟账户。
- 原 `iquant_qlib.py` 保留作参考，不直接修改为生产脚本。

### 阶段E：QMT真实绩效账本

- 复用影子模式的净值指标和报告展示代码，新增QMT结果读取适配器。
- 以券商`eod_snapshot.json`为准生成`qmt_nav.csv`，以真实成交生成`qmt_trades.csv`。
- 处理外部入金/出金登记、重复导入、缺失收盘快照和非本策略委托。
- 生成QMT真实收益、影子回放和基准归一化对比报告。

验收：连续样例可稳定生成收益、累计收益、回撤和超额指标；重复运行不重复记账，缺少真实账户事实时硬失败或明确标记缺失。

### 阶段F：模拟盘验收

- QMT先运行只读模式，核对账户和规划结果，不报单。
- 再运行模拟账户至少20个交易日。
- QMT每日独立保存执行结果并对中止、拒单、超卖或重复委托立即报警，不等待影子模式。
- 每个观察窗口结束后集中运行影子回放，批量检查资金不足、未知持仓、撤单、规划重放和最终账户差异。
- 模拟盘验收后另行讨论实盘，不在本阶段自动增加实盘开关。

## 8. 测试清单

- `signal.json`正常、篡改、过期、日期错位、版本错位和重复批次。
- 持仓评分Drop2、低分股票停牌/跌停后的顺延、门控清仓。
- Top100涨停/停牌跳过、候选不足、买不起一手和整手计算。
- 卖单全成、部分成交、拒单、超时撤单后买入预算重算。
- 买单全成、部分成交、拒单和超时撤单后保留现金。
- QMT在卖单或买单阶段崩溃后恢复，不重复报单。
- QMT账户快照与本地成交推算不一致时，以券商为准并报警。
- 收盘快照正常、缺失、重复、总资产缺失和日期错位。
- 外部入金/出金调整后的日收益，首日净值初始化和跨日累计收益。
- 真实成交费用/滑点汇总，未知费用不使用模拟值填充。
- 出现人工委托或其他策略成交时，绩效当日标记异常。
- QMT绩效重复导入幂等，QMT/影子/基准归一化对比曲线正确。
- 影子账本、QMT状态和对账程序互不写入。
- 现有影子测试、共享规划器测试和379日严格对账全部继续通过。

## 9. 明确不做

- 不使用MiniQMT、外部XtQuant或Mac盘中远程控制QMT。
- 不使用Syncthing、网盘或共享目录在Mac和Windows之间持续同步运行文件。
- 不让影子模式与QMT每日同步陪跑，也不配置正式影子每日调度。
- 不把完整Qlib、模型训练或GitHub数据更新放入QMT内置脚本。
- 不让影子成交结果决定QMT账户。
- 不把券商账户、委托或成交数据持续同步到公司Mac。
- 不在模拟盘验收前连接真实资金。

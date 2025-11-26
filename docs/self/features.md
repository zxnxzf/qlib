# Qlib 功能开发记录

本文档记录了在 Qlib 项目中新增和改进的功能。

---

## 目录

- [iQuant 实盘交易集成](#iquant-实盘交易集成)
  - [功能概述](#功能概述)
  - [技术架构](#技术架构)
  - [已实现功能](#已实现功能)
  - [核心文件](#核心文件)
  - [使用流程](#使用流程)
  - [已修复问题](#已修复问题)
- [Qlib 与 iQuant 数据一致性验证](#qlib-与-iquant-数据一致性验证)
  - [验证目标](#验证目标)
  - [技术方案](#技术方案)
  - [验证结果](#验证结果)
  - [核心代码](#核心代码)
  - [关键发现](#关键发现)

---

## iQuant 实盘交易集成

**实现时间**: 2025-11-20
**状态**: ✅ 已完成

### 功能概述

实现了 Qlib 量化平台与国信 iQuant 实盘交易系统的完整集成，支持从模型预测、选股、报价获取到实盘下单的全流程自动化。

### 技术架构

采用**两阶段握手协议**，通过文件和状态机实现 Qlib 与 iQuant 的数据同步：

```
┌─────────────┐                    ┌──────────────┐
│   qlib      │                    │   iQuant     │
│  (Python)   │                    │  (Python)    │
└──────┬──────┘                    └──────┬───────┘
       │                                  │
       │ 1. positions_needed              │
       ├─────────────────────────────────>│
       │                                  │
       │         2. positions_ready       │
       │<─────────────────────────────────┤
       │      (positions_live.csv)        │
       │                                  │
       │ 3. Phase1: 模型推理 + 选股        │
       │    symbols_ready                 │
       ├─────────────────────────────────>│
       │      (symbols_req.csv)           │
       │                                  │
       │         4. quotes_ready          │
       │<─────────────────────────────────┤
       │      (quotes_live.csv)           │
       │                                  │
       │ 5. Phase2: 计算份额 + 生成订单    │
       │    orders_ready                  │
       ├─────────────────────────────────>│
       │      (orders_to_exec.csv)        │
       │                                  │
       │         6. exec_done             │
       │<─────────────────────────────────┤
       │                                  │
```

#### 状态机流程

| 阶段 | 状态 | 负责方 | 动作 | 输出文件 |
|------|------|--------|------|---------|
| P0 | `positions_needed` | qlib | 请求持仓数据 | `state.json` |
| P1 | `positions_ready` | iQuant | 导出当前持仓 | `positions_live.csv` |
| P2 | `symbols_ready` | qlib | Phase1: T-1数据选股 | `symbols_req.csv` |
| P3 | `quotes_ready` | iQuant | 获取实时报价 | `quotes_live.csv` |
| P4 | `orders_ready` | qlib | Phase2: 计算订单 | `orders_to_exec.csv` |
| P5 | `exec_done` | iQuant | 实盘下单 | `orders_log.csv` |

### 已实现功能

#### 1. 双向数据同步

- ✅ **持仓同步**: 从 iQuant 读取真实持仓（股票代码、数量、成本价）
- ✅ **现金同步**: 自动获取账户可用资金（优先使用实际值，配置值作为备用）
- ✅ **报价同步**: 获取当日实时行情（last/bid1/ask1/涨跌停限价）

#### 2. 两阶段交易流程

**Phase 1: 模型选股**
- 使用 T-1 日数据进行模型推理
- TopkDropoutStrategy 选出候选股票
- 输出候选清单 `symbols_req.csv`

**Phase 2: 订单生成**
- 读取 iQuant 提供的实时报价
- 基于当日价格计算交易份额
- 考虑涨跌停保护
- 整手取整（100股为一手）
- 输出 `orders_to_exec.csv`

#### 3. 实盘交易支持

- ✅ **智能定价**: 买入使用 ask1→last→涨停价，卖出使用 bid1→last→跌停价
- ✅ **涨跌停保护**: 自动过滤无法交易的涨跌停股票
- ✅ **整手交易**: 自动按100股整手取整
- ✅ **DRY_RUN 模式**: 支持模拟测试，不实际下单
- ✅ **订单幂等**: 通过 order_id 确保订单不重复提交

#### 4. LiveExchange 实现

创建了专门的 `LiveExchange` 类（`qlib/backtest/live_exchange.py`）：
- 覆盖 `get_deal_price()` 使用实时报价
- 优先使用 `quotes_live.csv` 中的 bid1/ask1/last 价格
- 内置涨跌停保护逻辑
- 向后兼容回测模式

### 核心文件

| 文件路径 | 功能说明 |
|---------|---------|
| `examples/live_daily_predict.py` | qlib 侧实盘主流程（状态机、两阶段选股和下单） |
| `examples/iquant_qlib.py` | iQuant 侧脚本（持仓导出、报价获取、实盘下单） |
| `qlib/backtest/live_exchange.py` | LiveExchange 类（实时报价定价） |
| `qlib/contrib/strategy/order_generator.py` | 订单生成逻辑增强 |

### 使用流程

#### 前置条件

1. 已安装 Qlib 和依赖：
   ```bash
   conda activate qlib
   pip install pandas
   ```

2. 已配置 iQuant 账户 ID（在 `iquant_qlib.py` 中设置）

3. 已准备历史数据（用于模型推理）

#### 运行步骤

1. **启动 iQuant 脚本**（在 iQuant 客户端中）:
   ```python
   # 加载 examples/iquant_qlib.py (GBK 编码)
   # 设置 ACCOUNT_ID 和 STRATEGY_NAME
   ```

2. **运行 qlib 实盘脚本**:
   ```bash
   python examples/live_daily_predict.py
   ```

3. **观察状态变化**:
   ```
   positions_needed → positions_ready → symbols_ready →
   quotes_ready → orders_ready → exec_done
   ```

#### 文件输出

- `state.json`: 当前状态和版本号
- `positions_live.csv`: iQuant 导出的持仓数据
- `symbols_req.csv`: qlib 选出的候选股票
- `quotes_live.csv`: iQuant 提供的实时报价
- `orders_to_exec.csv`: qlib 生成的待执行订单
- `orders_log.csv`: iQuant 下单日志（可选）

### 已修复问题

#### Bug #1: iQuant 实盘下单失败 - passorder 返回 0

**问题**: 在交易时间内调用 `passorder` 下单时始终返回 0（失败），订单未进入券商系统。

**根本原因**: 缺少 `is_last_bar()` 检查，导致在历史回放阶段就执行了下单逻辑。iQuant 在策略运行时会先回放历史数据，在历史 bar 上的交易操作会被静默忽略。

**修复方案**:
```python
def handlebar(ContextInfo):
    # 获取 is_last_bar 函数
    is_last_bar_func = getattr(ContextInfo, 'is_last_bar', lambda: True)
    is_last = is_last_bar_func()

    # 只在实时 bar 执行
    if not is_last:
        print(f"[DEBUG] 非实时 bar，跳过执行")
        return

    # ... 下单逻辑 ...
```

**相关提交**: `43ef5eeb` - fix: 修复 iQuant 实盘下单失败问题

**经验教训**:
- iQuant 的 `handlebar()` 会先执行历史回放，再执行实时 bar
- **必须使用 `is_last_bar()` 区分**历史数据和实时数据
- 在历史 bar 上的交易操作会被静默忽略，不会有任何错误提示

#### Feature #1: 实盘账户现金自动获取

**问题**: 之前使用配置文件的固定现金值，无法反映真实账户余额。

**解决方案**: 从 iQuant 自动获取账户可用资金，优先使用实际值。

**实现要点**:

1. iQuant 侧调用 API 获取现金:
```python
def _fetch_account_cash(ContextInfo):
    data = get_trade_detail_data(acc_id, ACCOUNT_TYPE, "account")
    account_obj = data[0] if isinstance(data, (list, tuple)) else data
    if hasattr(account_obj, 'm_dAvailable'):
        cash = getattr(account_obj, 'm_dAvailable', None)
        return float(cash)
    return None
```

2. 在 `positions_live.csv` 中添加 CASH 行:
```csv
code,position,available,cost_price,last_price
SH600000,1000,1000,10.5,11.2
SZ000001,500,500,15.3,16.1
CASH,50000.00,50000.00,,
```

3. qlib 侧读取并使用实际现金:
```python
# 优先使用实际现金，否则回退到配置值
if cash_from_iquant is not None:
    actual_cash = cash_from_iquant
else:
    actual_cash = config_cash
```

**数据优先级**: 实际 iQuant 数据 > 配置默认值

---

#### Bug #2: hold_thresh (T+1) 在实盘中失效

**发现时间**: 2025-11-26
**修复提交**: `d5d3dc05` - fix: 修复 hold_thresh (T+1) 在实盘中失效的问题

**问题描述**:

Qlib 的 `hold_thresh` 参数用于控制最短持有天数（实现 A 股 T+1 限制），但在实盘场景下完全失效：

- **回测场景**: Position 对象包含 `count_day` 字段，T+1 限制正常工作 ✅
- **实盘场景**: 从 iQuant 读取的持仓缺少 `count_day` 信息，导致 `get_stock_count()` 返回 0 ❌

**实际影响**:

```python
# TopkDropoutStrategy 卖出前检查持有天数
if current_temp.get_stock_count(code, bar=time_per_step) < self.hold_thresh:
    continue  # 持有天数不足，跳过卖出

# 实盘场景
- 新买入股票: count_day 缺失 → 返回 0 < 1 → 跳过卖出 ✅ 正确
- 老持仓股票: count_day 缺失 → 返回 0 < 1 → 跳过卖出 ❌ 错误！（老持仓应该可以卖）
```

**根本原因**:

实盘初始化 Position 时，只设置了 `amount`，没有设置 `count_day`：

```python
# 修复前
position = Position(
    cash=total_cash,
    position_dict={code: {"amount": amount} for code, amount in holdings.items()},
    # ❌ 缺少 count_day 字段
)
```

**解决方案**:

**核心思路**: 本地维护 `holdings_history.json`，记录**由 qlib 买入的股票**及其买入日期：
- ✅ **老持仓**（history 中没有记录）→ 默认可卖（`hold_days = 101`）
- ✅ **新买入**（history 中有记录）→ 计算实际持有天数，严格遵守 T+1

**实现细节**:

1. **持仓历史文件** (`predictions/holdings_history.json`):
```json
{
    "601318.SH": {
        "buy_date": "2025-01-15",
        "amount": 200
    }
}
```

2. **计算持有天数** (老持仓默认可卖):
```python
def _calculate_hold_days(current_holdings, today_str, hold_thresh=1):
    history = _load_holdings_history()

    # 自动清理已卖出的股票
    for code in list(history.keys()):
        if code not in current_holdings:
            del history[code]  # 已卖出，删除记录

    # 计算持有天数
    for code, amount in current_holdings.items():
        if code in history:
            # 有记录：计算实际持有天数
            buy_date = history[code]["buy_date"]
            hold_days = (today - buy_date).days
        else:
            # 无记录：老持仓，默认可卖
            hold_days = hold_thresh + 100  # 101 天
```

3. **初始化 Position 时设置 count_day**:
```python
# 修复后
hold_days_dict = _calculate_hold_days(holdings, today, hold_thresh=1)

position_dict = {}
for code, amount in holdings.items():
    position_dict[code] = {
        "amount": amount,
        "count_day": hold_days_dict[code],  # ✅ 设置持有天数
    }

position = Position(cash=total_cash, position_dict=position_dict)
```

4. **记录买入订单**:
```python
# 生成订单后，记录买入到历史
buy_orders = orders_df[orders_df["action"] == "买入"]
if len(buy_orders) > 0:
    _update_holdings_history_after_buy(buy_orders, today)
```

**更新时机**:

| 操作 | 时机 | 说明 |
|------|------|------|
| 📖 **读取** | Phase2 开始 | 计算持有天数时读取 |
| ✏️ **新增** | 生成买入订单后 | 记录买入日期 |
| 🗑️ **删除** | Phase2 开始（自动） | 清理已卖出股票 |

**测试场景**:

**Day 1 - 首次运行（老持仓）**:
```
持仓: 600519.SH (100股)
历史: (空)
结果: hold_days=101，可以卖出 ✅

生成订单: 买入 601318.SH (200股)
更新历史: {"601318.SH": {"buy_date": "2025-01-15", ...}}
```

**Day 2 - T+1 检查**:
```
持仓: 601318.SH (200股)
历史: {"601318.SH": {"buy_date": "2025-01-15", ...}}
结果: hold_days=1，满足 hold_thresh=1，可以卖出 ✅
```

**Day 3 - 自动清理**:
```
持仓: (601318.SH 已卖出)
历史: {"601318.SH": ...}
执行: 自动检测并删除 601318.SH
输出: [清理] 已卖出股票: 601318.SH
```

**核心优势**:

- ✅ **首次运行友好**: 老持仓可以正常卖出
- ✅ **严格 T+1 限制**: 新买入必须持有满足天数
- ✅ **自动维护**: 无需手动清理，防止文件膨胀
- ✅ **健壮性强**: 文件丢失不影响系统运行（默认可卖）
- ✅ **更新时机合理**: 订单生成后立即记录，保守安全

**相关文件**:
- `examples/live_daily_predict.py` - 添加持仓历史管理逻辑
- `predictions/holdings_history.json` - 自动生成的持仓历史文件

**经验教训**:
- 实盘场景下，Position 对象的初始化需要完整设置所有必要字段
- 对于缺失的历史数据，应采用保守策略（宁可多限制，不可违规）
- 自动清理机制避免了状态文件的无限增长

---

## 后续规划

- [ ] 支持更多订单类型（限价单、止损单等）
- [ ] 实现订单状态追踪和回调处理
- [ ] 增加风控规则（单笔限额、持仓比例等）
- [ ] 支持多账户管理
- [ ] 完善异常处理和重试机制

---

## Qlib 与 iQuant 数据一致性验证

**实现时间**: 2025-11-25
**状态**: ✅ 已完成

### 验证目标

验证 Qlib 社区数据与 iQuant 实盘数据的一致性，确保使用 Qlib 训练的模型在 iQuant 实盘环境中能获得相同的价格数据。

### 技术方案

#### 方案选择：使用原始价格（未复权）

**问题**: Qlib 默认使用复权价格，iQuant 实盘使用原始价格，导致价格数量级差异巨大。

**解决方案**: 让 Qlib 也使用原始价格（未复权），与 iQuant 保持一致。

| 项目 | 复权价格 | 原始价格 |
|------|---------|---------|
| 贵州茅台 | 198.85 元 | 1419.20 元 |
| 比亚迪 | 13.08 元 | 105.69 元 |
| 相对差异 | **86%** | **< 0.0001%** ✅ |

#### Qlib 侧实现

**使用 `$close / $factor` 还原为原始价格**

```python
# test_claude_code/export_qlib_data.py

# 错误方式：使用复权价格
data = D.features(stocks, ["$close"], start_time, end_time)

# 正确方式：使用原始价格（未复权）
data = D.features(stocks, ["$close / $factor"], start_time, end_time)
```

**说明**:
- `$close`: 调整后价格（已复权）
- `$factor`: 复权因子
- `$close / $factor`: 原始价格（未复权，与 iQuant 一致）

#### iQuant 侧实现

**关键发现：回测中获取历史数据的正确方法**

经过多次尝试，找到了在回测模式下获取历史收盘价的正确 API：

##### ❌ 错误方法 1: `get_full_tick`

```python
# 问题：返回实时快照，不是历史数据
data = ContextInfo.get_full_tick(stock_list)
```

**现象**: 所有日期返回相同的价格（脚本运行时刻的实时行情）

**原因**: `get_full_tick` 在回测中仍然调用实时行情接口，返回的是当前时刻的快照，而非历史上某个日期的真实数据。

##### ❌ 错误方法 2: `get_market_data_ex`

```python
# 问题：参数错误，返回空数据
data = ContextInfo.get_market_data_ex(
    ['close'],
    [stock],
    '1d',
    start_time=date_str,
    end_time=date_str
)
```

**现象**:
- Python argument types did not match C++ signature
- 或返回空字典/空列表

**原因**:
1. 需要使用位置参数而非关键字参数
2. 即使改为位置参数，在回测中仍然无法获取当前 bar 的数据

##### ✅ 正确方法: `get_history_data`

```python
# examples/export_iquant_data.py

def handlebar(ContextInfo):
    # 先设置股票池（必须）
    ContextInfo.set_universe(stock_list)

    # 获取当前 bar 的历史数据
    hisdict = ContextInfo.get_history_data(
        1,        # len: 获取 1 根 K 线（当前 bar）
        '1d',     # period: 日线
        'close',  # field: 收盘价
        0         # dividend_type: 0=不复权, 1=前复权, 2=后复权
    )

    # hisdict 是字典: {股票代码: [收盘价]}
    for stock in stock_list:
        if stock in hisdict:
            close_data = hisdict[stock]
            close_price = float(close_data[-1])  # 取最后一个值
```

**为什么这个方法正确**:
1. **专为回测设计**: 在每个 bar 上自动返回该 bar 对应日期的历史数据
2. **无需指定日期**: 自动匹配当前 bar 的时间
3. **返回格式稳定**: 字典格式，key 是股票代码，value 是价格列表

### 验证结果

**对比统计**:
```
对比记录数: 75 条（15 个交易日 × 5 只股票）
平均相对差异: 0.0000%
最大相对差异: 0.0000%
数据相关系数: 1.00000000 (完美相关)
```

**最大差异的 5 条记录**:

| 日期 | 股票 | qlib 价格 | iQuant 价格 | 差异 |
|------|------|----------|-----------|------|
| 2025-11-06 | 600519.SH | 1435.1299 | 1435.13 | 0.0001 |
| 2025-11-12 | 600519.SH | 1465.1499 | 1465.15 | 0.0001 |
| 2025-10-13 | 600519.SH | 1419.2001 | 1419.20 | 0.0001 |

**结论**: 差异只有 0.0001 元（浮点数精度），数据完全一致 ✅

### 核心代码

#### 1. Qlib 数据导出

```python
# test_claude_code/export_qlib_data.py

import qlib
from qlib.data import D

# 初始化
qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region="cn")

# 获取原始价格（未复权）
stocks = ["sh600519", "sz002594", "sh600036"]
data = D.features(
    stocks,
    ["$close / $factor"],  # 关键：除以复权因子
    start_time="2025-10-11",
    end_time="2025-11-20"
)

# 保存到 CSV
df = data.reset_index()
df.to_csv("qlib_data.csv", index=False)
```

#### 2. iQuant 回测数据导出

```python
# examples/export_iquant_data.py (GBK 编码)

def init(ContextInfo):
    # 读取股票列表
    ContextInfo._stocks = ["000858.SZ", "600519.SH", "002594.SZ"]
    ContextInfo._collected_data = []

def handlebar(ContextInfo):
    # 获取当前日期
    timetag = ContextInfo.get_bar_timetag(ContextInfo.barpos)
    date_str = datetime.fromtimestamp(timetag / 1000).strftime('%Y-%m-%d')

    # 设置股票池（必须先设置）
    ContextInfo.set_universe(ContextInfo._stocks)

    # 获取当前 bar 的收盘价
    hisdict = ContextInfo.get_history_data(
        1,        # 获取 1 根 K 线
        '1d',     # 日线
        'close',  # 收盘价
        0         # 不复权
    )

    # 解析数据
    for stock in ContextInfo._stocks:
        if stock in hisdict:
            close_data = hisdict[stock]
            if isinstance(close_data, (list, tuple)) and len(close_data) > 0:
                close_price = float(close_data[-1])
                ContextInfo._collected_data.append({
                    'date': date_str,
                    'stock_code': stock,
                    'close': close_price
                })

    # 保存到 CSV（在最后一个 bar 或累计够一定数量）
    if len(ContextInfo._collected_data) >= 40:
        df = pd.DataFrame(ContextInfo._collected_data)
        df.to_csv("iquant_data.csv", index=False)
```

#### 3. 数据对比分析

```python
# test_claude_code/compare_data.py

import pandas as pd

# 加载数据
qlib_df = pd.read_csv("qlib_data.csv")
iquant_df = pd.read_csv("iquant_data.csv")

# 合并数据
merged = pd.merge(
    qlib_df.rename(columns={"close": "qlib_close"}),
    iquant_df.rename(columns={"close": "iquant_close"}),
    on=["date", "stock_code"],
    how="outer"
)

# 计算差异
merged["abs_diff"] = abs(merged["qlib_close"] - merged["iquant_close"])
merged["rel_diff_pct"] = (merged["abs_diff"] / merged["iquant_close"]) * 100

# 统计
print(f"平均相对差异: {merged['rel_diff_pct'].mean():.4f}%")
print(f"数据相关系数: {merged['qlib_close'].corr(merged['iquant_close']):.8f}")
```

### 关键发现

#### 1. iQuant 回测 API 对比

| API | 用途 | 回测中是否可用 | 返回数据类型 |
|-----|------|---------------|-------------|
| `get_full_tick` | 获取实时快照 | ❌ 返回当前实时数据，非历史 | 字典 {code: tick_data} |
| `get_market_data` | 获取历史行情 | ⚠️ 参数复杂，难以使用 | DataFrame |
| `get_market_data_ex` | 获取历史行情（扩展版）| ⚠️ 参数签名问题 | 字典 {code: [[time, value]]} |
| `get_history_data` | 获取历史 K 线 | ✅ **推荐使用** | 字典 {code: [values]} |

#### 2. `get_history_data` 使用要点

**必须先设置股票池**:
```python
ContextInfo.set_universe(stock_list)  # 必须先调用
hisdict = ContextInfo.get_history_data(1, '1d', 'close', 0)
```

**参数说明**:
- `len` (int): 获取多少根 K 线（1 = 当前 bar）
- `period` (str): 周期，可选值: `'1d'`, `'1m'`, `'5m'`, `'1h'`, `'1w'` 等
- `field` (str): 字段，可选值: `'open'`, `'high'`, `'low'`, `'close'`, `'quoter'`
- `dividend_type` (int): 复权方式
  - `0`: 不复权（与 iQuant 实盘一致）
  - `1`: 向前复权
  - `2`: 向后复权

**返回数据格式**:
```python
{
    "000858.SZ": [119.85],      # 列表，包含 len 个值
    "600519.SH": [1419.20],
    "002594.SZ": [105.69]
}
```

#### 3. 常见问题

**Q: 为什么有些 bar 返回空数据？**

A: 可能是非交易日，或者 iQuant 数据未下载到本地。解决方法：
```python
# 在 init() 中预下载历史数据
ContextInfo.download_history_data(
    stock_code=stock_list,
    period='1d',
    start_time='2025-10-11',
    end_time='2025-11-20'
)
```

**Q: `get_market_data_ex` 为什么总是失败？**

A: iQuant 的 Python API 底层是 C++，必须使用位置参数：
```python
# ❌ 错误：使用关键字参数
data = ContextInfo.get_market_data_ex(
    fields=['close'],
    stock_code=[stock],
    period='1d'
)

# ✅ 正确：使用位置参数
data = ContextInfo.get_market_data_ex(
    ['close'],     # 位置 0
    [stock],       # 位置 1
    '1d',          # 位置 2
    start_time=...,
    end_time=...
)
```

**Q: `get_full_tick` 在回测中能用吗？**

A: 可以调用，但返回的是**实时快照而非历史数据**。所有日期会返回相同的价格（脚本运行时刻的行情），不适合回测数据导出。

#### 4. 调试技巧

**打印返回数据结构**:
```python
data = ContextInfo.get_history_data(1, '1d', 'close', 0)
print(f"类型: {type(data)}")
print(f"键: {list(data.keys()) if isinstance(data, dict) else 'N/A'}")
print(f"第一个值: {data[list(data.keys())[0]] if data else 'Empty'}")
```

**对比不同日期的价格**:
```python
# 在每个 bar 打印价格，确认是否变化
print(f"日期: {date_str}, 贵州茅台: {hisdict.get('600519.SH', 'N/A')}")
```

### 相关文件

| 文件 | 说明 |
|------|------|
| `test_claude_code/export_qlib_data.py` | Qlib 数据导出（原始价格） |
| `test_claude_code/compare_data.py` | 数据对比分析工具 |
| `examples/export_iquant_data.py` | iQuant 回测数据导出（GBK 编码） |
| `examples/simple_get_price.py` | iQuant API 测试示例 |
| `predictions/qlib_data.csv` | Qlib 导出的数据 |
| `predictions/iquant_data.csv` | iQuant 导出的数据 |
| `predictions/data_comparison.csv` | 对比结果详细数据 |

### Git 提交

```bash
git commit: feat: qlib 与 iQuant 数据一致性验证
```

**主要改动**:
- 新增 Qlib 原始价格导出脚本
- 新增 iQuant 回测数据导出脚本（使用 `get_history_data`）
- 新增数据对比分析工具
- 验证结果：数据完全一致（相关系数 1.0）

---

## LiveTopkStrategy - 小资金优化策略

**实现时间**: 2025-11-26
**状态**: ✅ 已完成

### 功能概述

为小资金账户创建优化策略，通过**两轮预算分配**机制最大化买入股票数量和资金利用率，解决小资金账户买不起高价股导致资金闲置的问题。

### 核心问题

**原有策略（TopkDropoutStrategy）的局限**:
- 等分预算买入topk只股票
- 高价股可能买不到1手（100股），直接跳过
- 导致资金闲置，买入股票数量少

**示例问题**:
```
总资金: 10万, topk=10, risk_degree=0.95
预算: 9.5万 / 10 = 9,500元/股

候选股票价格:
- 贵州茅台: 1,680元 → 可买 5股 < 100股 ❌ 跳过
- 比亚迪: 300元 → 可买 31股 < 100股 ❌ 跳过
- 宁德时代: 200元 → 可买 47股 < 100股 ❌ 跳过
...

结果: 10只候选中只买到2-3只，资金使用率不足50%
```

### 解决方案：两轮预算分配

**核心思路**: 先筛选可负担股票，再重新等分预算

#### Round 1 - 可负担性筛选
```python
# 假设等分预算
initial_budget = total_cash * risk_degree / topk

# 筛选能买到至少100股的股票
affordable_stocks = []
for stock in candidates:
    shares = initial_budget / price[stock]
    shares = round_to_lot(shares)  # 整手取整
    if shares >= 100:  # 至少1手
        affordable_stocks.append(stock)
```

#### Round 2 - 预算重新分配
```python
# 用总预算重新等分给筛选出的股票
final_budget = total_cash * risk_degree / len(affordable_stocks)

# 用更高的单股预算买入
for stock in affordable_stocks:
    shares = final_budget / price[stock]
    shares = round_to_lot(shares)
    place_order(stock, shares)
```

#### 效果对比

**示例场景**: 总资金10万，topk=20，risk_degree=0.95

| 策略 | Round1筛选 | Round2重分配 | 结果 |
|-----|-----------|------------|------|
| **TopkDropoutStrategy** | N/A | 9.5万/20=4,750元/股 | 只买到5只，资金使用率45% |
| **LiveTopkStrategy** | 4,750元/股筛选 → 保留8只 | 9.5万/8=11,875元/股 | 买到8只，资金使用率95% |

**提升**:
- ✅ 买入股票数: 5只 → 8只 (+60%)
- ✅ 资金使用率: 45% → 95% (+111%)
- ✅ 单股预算: 4,750元 → 11,875元 (+150%)

### 实现细节

#### 1. 创建 LiveTopkStrategy 类

**文件**: `qlib/contrib/strategy/live_strategy.py`

```python
from .signal_strategy import TopkDropoutStrategy

class LiveTopkStrategy(TopkDropoutStrategy):
    def __init__(
        self,
        *,
        min_affordable_shares: int = 100,        # 最小可负担股数（1手）
        enable_affordability_filter: bool = True, # 启用两轮分配
        **kwargs  # 父类所有参数
    ):
        super().__init__(**kwargs)
        self.min_affordable_shares = min_affordable_shares
        self.enable_affordability_filter = enable_affordability_filter

    def generate_trade_decision(self, execute_result=None):
        # 如果禁用，直接使用父类逻辑
        if not self.enable_affordability_filter:
            return super().generate_trade_decision(execute_result)

        # ... Round 1: 筛选可负担股票 ...
        # ... Round 2: 重新分配预算 ...
```

#### 2. 配置参数

**DEFAULT_CONFIG 配置** (`live_daily_predict.py`):
```python
"trading": {
    # ... 其他配置 ...
    "risk_degree": 0.05,  # 小资金时，通过调整 risk_degree 控制实际使用金额

    # LiveTopkStrategy 相关配置
    "use_live_topk_strategy": False,  # 是否启用（默认关闭）
    "min_affordable_shares": 100,     # 最小可负担股数（1手）
}
```

**TradingConfig 类** (`daily_predict.py`):
```python
@dataclass
class TradingConfig:
    # ... 其他字段 ...

    # LiveTopkStrategy 相关参数
    use_live_topk_strategy: bool = False
    min_affordable_shares: int = 100
```

#### 3. 策略实例化

**自动选择策略** (`live_daily_predict.py` 约760行):
```python
# 根据配置选择策略类
use_live_topk = self.trading_cfg.use_live_topk_strategy
min_afford_shares = self.trading_cfg.min_affordable_shares

if use_live_topk:
    from qlib.contrib.strategy.live_strategy import LiveTopkStrategy
    print("[live] 使用 LiveTopkStrategy（两轮预算分配优化）")
    strategy = LiveTopkStrategy(
        signal=signal,
        topk=self.prediction_cfg.top_k,
        n_drop=self.trading_cfg.n_drop,
        # ... 其他父类参数 ...
        min_affordable_shares=min_afford_shares,
        enable_affordability_filter=True,
    )
else:
    from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy
    print("[live] 使用 TopkDropoutStrategy（标准策略）")
    strategy = TopkDropoutStrategy(...)
```

### 使用方法

#### 方式1: 修改 DEFAULT_CONFIG（推荐）

**编辑** `examples/live_daily_predict.py`:
```python
DEFAULT_CONFIG = {
    # ... 其他配置 ...
    "trading": {
        "total_cash": 50000,  # 或从 iQuant 自动获取
        "risk_degree": 0.05,  # 控制实际使用资金（100万*0.05=5万）
        "use_live_topk_strategy": True,  # ✅ 启用 LiveTopkStrategy
        "min_affordable_shares": 100,
    }
}
```

**运行**:
```bash
python examples/live_daily_predict.py
```

#### 方式2: 使用外部配置文件

**创建** `config_live_small_cap.json`:
```json
{
  "trading": {
    "total_cash": 50000,
    "risk_degree": 0.95,
    "use_live_topk_strategy": true,
    "min_affordable_shares": 100
  }
}
```

**运行**:
```bash
python examples/live_daily_predict.py --config config_live_small_cap.json
```

### 预期日志输出

```
[live] 使用 LiveTopkStrategy（两轮预算分配优化）

[LiveTopk] Round 1: 可负担性筛选
   现金: 50000.00, 风险度: 0.95, 候选数: 20
   初始单股预算: 2375.00 元
   [过滤] SH600519: 价格=1680.00, 可买=1股 < 100
   [过滤] SZ002594: 价格=300.00, 可买=7股 < 100
   ... (12只过滤)

[LiveTopk] Round 2: 预算重新分配
   20 候选 → 8 可买入
   预算调整: 2375 → 5938 元/股

[live] 生成买入订单: 8 条
[live] 预计买入金额: 47,504 元
[live] 资金使用率: 95.0%
```

### 关键文件

| 文件 | 说明 |
|------|------|
| `qlib/contrib/strategy/live_strategy.py` | LiveTopkStrategy 核心实现 |
| `qlib/contrib/strategy/__init__.py` | 导出 LiveTopkStrategy |
| `examples/daily_predict.py` | TradingConfig 类定义（新增字段） |
| `examples/live_daily_predict.py` | 配置和策略实例化逻辑 |

### 适用场景

✅ **适合**:
- 小资金账户（<10万）
- 高价股较多的股票池（如沪深300）
- 希望提高资金使用率和分散度

⚠️ **不适合**:
- 大资金账户（>50万）
- 全是低价股的股票池
- 需要严格控制持仓数量的策略

### 权衡考虑

**优点**:
- ✅ 提高买入股票数量（更好的分散度）
- ✅ 减少资金闲置（更高的资金使用率）
- ✅ 灵活适配小资金场景

**缺点**:
- ⚠️ 可能跳过高质量但价格昂贵的股票
- ⚠️ 如果可负担股票太少，仍可能集中持仓
- ⚠️ 两轮筛选增加少量计算开销

### 经验教训

1. **资金来源**: total_cash 来自 iQuant 实际账户（positions_live.csv 的 CASH 行），通过 risk_degree 控制实际使用比例
2. **配置灵活性**: 通过 enable_affordability_filter 参数可随时切换回标准策略
3. **向后兼容**: 继承 TopkDropoutStrategy，保留所有父类功能（T+1限制、卖出逻辑等）

### Git 提交

```bash
git commit: feat: 实现 LiveTopkStrategy 小资金优化策略
```

**主要改动**:
- 新增 `qlib/contrib/strategy/live_strategy.py` (~350行)
- 更新 `qlib/contrib/strategy/__init__.py` 导出
- 修改 `examples/daily_predict.py` TradingConfig 添加字段
- 修改 `examples/live_daily_predict.py` 配置和策略实例化
- 更新文档 `docs/self/features.md`

---

*最后更新: 2025-11-25*
**文档维护**: 每次实现新功能并验证通过后，及时更新本文档。

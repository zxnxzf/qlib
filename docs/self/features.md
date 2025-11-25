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

## 后续规划

- [ ] 支持更多订单类型（限价单、止损单等）
- [ ] 实现订单状态追踪和回调处理
- [ ] 增加风控规则（单笔限额、持仓比例等）
- [ ] 支持多账户管理
- [ ] 完善异常处理和重试机制

---

**文档维护**: 每次实现新功能并验证通过后，及时更新本文档。

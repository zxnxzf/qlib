# coding: gbk
# iQuant-side script handling three handshake phases with qlib:
#   1) positions_needed -> export holdings -> positions_ready
#   2) symbols_ready    -> export live quotes -> quotes_ready
#   3) orders_ready     -> execute orders -> exec_done/exec_failed

import os
import json
import time
import pandas as pd

# ===== Paths (edit to your environment) =====
POSITIONS_CSV_ABS_PATH = r"D:\code\qlib\qlib\examples\predictions\positions_live.csv"
QUOTES_CSV_ABS_PATH = r"D:\code\qlib\qlib\examples\predictions\quotes_live.csv"
SYMBOLS_REQ_ABS_PATH = r"D:\code\qlib\qlib\examples\predictions\symbols_req.csv"
ORDERS_CSV_ABS_PATH = r"D:\code\qlib\qlib\examples\predictions\orders_to_exec.csv"
STATE_JSON_PATH = r"D:\code\qlib\qlib\examples\predictions\state.json"
# ============================================

# ---------- runtime flags ----------
DRY_RUN = False
ACCOUNT_ID = "410015004039"
ACCOUNT_TYPE = "STOCK"        # ✅ 修复：必须大写 FUTURE/STOCK/CREDIT/HUGANGTONG/SHENGANGTONG/STOCK_OPTION
POSITION_STRATEGY_NAME = ""   # 策略持仓，留空可
USE_LIMIT_PRICE = False
ENFORCE_BUY_100_LOT = True
ROUND_SELL_TO_100 = False
PRINT_HEAD_ROWS = 10
TARGET_VERSION = None
POLL_INTERVAL = 5             # 轮询间隔（秒）
MAX_POLL_COUNT = 360          # 最大轮询次数（360次 * 5秒 = 30分钟）

# ---------- state ----------
_ORDER_DF = None
_DONE_IDS = set()
_ERROR_ONCE = False
_FIRED = False


def _normalize_code(code: str) -> str:
    if pd.isna(code):
        return ""
    s = str(code).strip().upper()
    if s.endswith(".SH") or s.endswith(".SZ"):
        return s
    if s.startswith("SH") and len(s) == 8 and s[2:].isdigit():
        return f"{s[2:]}.SH"
    if s.startswith("SZ") and len(s) == 8 and s[2:].isdigit():
        return f"{s[2:]}.SZ"
    if len(s) == 6 and s.isdigit():
        return s
    return s


def _ensure_parent(path: str):
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)


def _orders_path() -> str:
    if ORDERS_CSV_ABS_PATH and os.path.isabs(ORDERS_CSV_ABS_PATH):
        return ORDERS_CSV_ABS_PATH
    raise ValueError("configure ORDERS_CSV_ABS_PATH as absolute path")


def _load_orders() -> pd.DataFrame:
    global _ORDER_DF
    if _ORDER_DF is not None:
        return _ORDER_DF
    path = _orders_path()
    if not os.path.isfile(path):
        raise FileNotFoundError(f"orders file not found: {path}")
    df = pd.read_csv(path, encoding="utf-8-sig", skipinitialspace=True)
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "code" not in df.columns and "stock" in df.columns:
        df = df.rename(columns={"stock": "code"})
    required = {"order_id", "code", "action", "shares"}
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"orders csv missing columns: {missing}")
    df["code"] = df["code"].map(_normalize_code)
    df["order_id"] = df["order_id"].astype(str).str.strip()
    df["action"] = df["action"].astype(str).str.strip()
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce").fillna(0).astype(int)
    if "price" in df.columns:
        df["price"] = pd.to_numeric(df.get("price"), errors="coerce")
    df = df[(df["code"] != "") & (df["shares"] != 0)].copy()
    _ORDER_DF = df
    return _ORDER_DF


def _read_state() -> dict:
    if not STATE_JSON_PATH:
        print("[DEBUG] STATE_JSON_PATH 未配置")
        return {}
    print(f"[DEBUG] 尝试读取 state.json: {STATE_JSON_PATH}")
    if not os.path.isfile(STATE_JSON_PATH):
        print(f"[DEBUG] state.json 不存在: {STATE_JSON_PATH}")
        return {}
    try:
        with open(STATE_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[DEBUG] state.json 读取成功: {data}")
        return data
    except Exception as e:
        print(f"[WARN] state.json 读取失败: {e}")
        import traceback
        traceback.print_exc()
        return {}


def _write_state(phase: str, version: str, extra: dict = None):
    if not STATE_JSON_PATH:
        return
    payload = {"phase": phase, "version": version, "timestamp": pd.Timestamp.utcnow().isoformat()}
    if extra:
        payload.update(extra)
    tmp = STATE_JSON_PATH + ".tmp"
    _ensure_parent(STATE_JSON_PATH)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_JSON_PATH)
    print("[DEBUG] write state =>", payload)


def _sign_by_action(action: str, qty: int) -> int:
    a = str(action).strip().upper()
    if ("SELL" in a) or ("卖" in action):
        return -abs(qty)
    return abs(qty)


def _round_shares(qty: int, is_sell: bool) -> int:
    if qty == 0:
        return 0
    if is_sell:
        if ROUND_SELL_TO_100:
            return (abs(qty) // 100) * 100 * (-1 if qty < 0 else 1)
        return qty
    if ENFORCE_BUY_100_LOT:
        return ((abs(qty) // 100) * 100) * (1 if qty > 0 else -1)
    return qty


def _extract_order_error(ret) -> str:
    if ret is None:
        return "order_shares returned None"
    for attr in ("m_nErrorCode", "error_code", "ErrorID"):
        code = getattr(ret, attr, None)
        if code not in (None, 0):
            msg = getattr(ret, "m_strErrorMsg", getattr(ret, "error_msg", getattr(ret, "ErrorMsg", ""))) or "unknown"
            return f"error_code={code}, msg={msg}"
    if isinstance(ret, bool):
        return "" if ret else "order_shares returned False"
    if isinstance(ret, (int, float)):
        return "" if ret > 0 else f"order_shares returned {ret}"
    if isinstance(ret, (tuple, list)) and ret:
        status = ret[0]
        if isinstance(status, (int, float)) and status <= 0:
            return f"order_shares tuple status={status}"
    return ""


def _call_order_shares(code, qty, price, order_id, ContextInfo):
    """使用 passorder 下单，格式与 iquant_lizi.py 完全一致"""
    acc_id = getattr(ContextInfo, "accid", None) or (ACCOUNT_ID or None)
    is_sell = qty < 0
    opType = 24 if is_sell else 23
    volume = abs(qty)
    prType = 5  # 最新价
    use_price = -1

    # ========== passorder 调用前诊断 ==========
    print(f"\n{'='*60}")
    print(f"[DEBUG][passorder] ===== 准备调用 passorder =====")
    print(f"[DEBUG][passorder] 股票代码: {code}")
    print(f"[DEBUG][passorder] 数量: {qty} (volume={volume})")
    print(f"[DEBUG][passorder] 方向: {'卖出' if is_sell else '买入'} (opType={opType})")
    print(f"[DEBUG][passorder] 价格类型: prType={prType} (5=最新价)")
    print(f"[DEBUG][passorder] 价格: {use_price}")
    print(f"[DEBUG][passorder] 账户: {acc_id}")
    print(f"[DEBUG][passorder] ContextInfo 类型: {type(ContextInfo)}")
    print(f"[DEBUG][passorder] ContextInfo.accid: {getattr(ContextInfo, 'accid', 'NOT SET')}")

    # 检查 passorder 函数是否存在
    try:
        passorder_exists = callable(passorder)
        print(f"[DEBUG][passorder] passorder 函数存在: {passorder_exists}")
    except NameError:
        print(f"[ERROR][passorder] ❌ passorder 函数不存在！")
        return None

    print(f"[DEBUG][passorder] 开始调用 passorder...")
    print(f"{'='*60}\n")

    # ========== 调用 passorder ==========
    ret = passorder(opType, 1101, acc_id, code, prType, use_price, volume, 'qlib_live', 1, '', ContextInfo)

    # ========== passorder 调用后诊断 ==========
    print(f"\n{'-'*60}")
    print(f"[DEBUG][passorder] ===== passorder 执行完毕 =====")
    print(f"[DEBUG][passorder] 返回值类型: {type(ret)}")
    print(f"[DEBUG][passorder] 返回值: {ret}")

    if hasattr(ret, '__dict__'):
        print(f"[DEBUG][passorder] 返回对象属性: {ret.__dict__}")

    if isinstance(ret, int):
        if ret > 0:
            print(f"[DEBUG][passorder] ✅ 返回 {ret} (>0)，可能成功")
        elif ret == 0:
            print(f"[DEBUG][passorder] ❌ 返回 0，下单失败（订单未进入系统）")
        else:
            print(f"[DEBUG][passorder] ❌ 返回 {ret} (<0)，下单失败")

    print(f"{'-'*60}\n")

    return ret


def _print_overview(df: pd.DataFrame):
    print("[iQuant] ===== orders preview =====")
    print("[iQuant] path:", _orders_path())
    print("[iQuant] columns:", list(df.columns))
    print(df.head(PRINT_HEAD_ROWS).to_string(index=False))
    print("[iQuant] rows:", len(df))
    print("[iQuant] ==========================")


def _place_orders(df: pd.DataFrame, ContextInfo, version: str):
    global _DONE_IDS
    for _, row in df.iterrows():
        oid = str(row["order_id"])
        if oid in _DONE_IDS:
            continue
        code = str(row["code"])
        raw_qty = int(row["shares"])
        signed_qty = _sign_by_action(row["action"], raw_qty)
        is_sell = signed_qty < 0
        final_qty = _round_shares(signed_qty, is_sell)
        price = float(row["price"]) if "price" in row and pd.notna(row.get("price")) else None

        if final_qty == 0:
            print(f"[iQuant][SKIP] {code} 原计划 {raw_qty} 股 -> 取整后 0, order_id={oid}")
            _DONE_IDS.add(oid)
            continue

        side = "卖出" if final_qty < 0 else "买入"
        if DRY_RUN:
            print(f"[DRY_RUN] {code} {side} {abs(final_qty)} 股 price={price if price else '市价'} (order_id={oid}, ver={version})")
            _DONE_IDS.add(oid)
            continue

        try:
            # ✅ 修复：passorder 无返回值，不应依赖返回值判断成功
            # 根据 iQuant API 文档，passorder 是无返回值函数，下单成功与否需要通过查询委托来确认
            _call_order_shares(code, final_qty, price, oid, ContextInfo)
            print(f"[iQuant] 已发送下单请求 {code} {side} {abs(final_qty)} 股 (order_id={oid}, ver={version})")
            print(f"[iQuant][INFO] 请在 iQuant 客户端查看【委托】确认订单是否成功")
        except Exception as err:
            print(f"[iQuant][ERROR] {code} {side} {abs(final_qty)} 股 下单异常: {err} (order_id={oid}, ver={version})")

        _DONE_IDS.add(oid)


# ---------- export helpers ----------
def _fetch_positions(ContextInfo):
    """
    ✅ 修复：正确调用 iQuant 的 get_trade_detail_data 获取持仓
    """
    acc_id = ACCOUNT_ID or getattr(ContextInfo, "accid", None)
    print(f"[DEBUG] _fetch_positions: acc_id={acc_id}, ACCOUNT_TYPE={ACCOUNT_TYPE}")
    if not acc_id:
        print("[WARN] ACCOUNT_ID is not set")
        return None

    try:
        # ✅ 修复：第三个参数必须是小写的 "position"
        print(f"[DEBUG] calling get_trade_detail_data(acc_id={acc_id}, account_type={ACCOUNT_TYPE}, data_type='position', strategy_name={POSITION_STRATEGY_NAME or 'None'})")
        if POSITION_STRATEGY_NAME:
            data = get_trade_detail_data(acc_id, ACCOUNT_TYPE, "position", POSITION_STRATEGY_NAME)
        else:
            data = get_trade_detail_data(acc_id, ACCOUNT_TYPE, "position")

        print(f"[DEBUG] get_trade_detail_data returned: type={type(data)}, data={data}")
        if data:
            print(f"[DEBUG] fetch holdings via get_trade_detail_data, count={len(data)}")
            return data
        else:
            print("[WARN] get_trade_detail_data returned empty list")
            return None
    except NameError as err:
        print(f"[ERROR] get_trade_detail_data not found in iQuant environment: {err}")
        print("[INFO] Please make sure this script runs in iQuant strategy editor")
        return None
    except Exception as err:
        print(f"[WARN] get_trade_detail_data failed: {err}")
        import traceback
        traceback.print_exc()
        return None


def _convert_positions(raw):
    """
    ✅ 修复：正确处理 iQuant position 对象的属性
    """
    print(f"[DEBUG] _convert_positions: raw type={type(raw)}, length={len(raw) if hasattr(raw, '__len__') else 'N/A'}")

    if isinstance(raw, pd.DataFrame):
        records = raw.to_dict("records")
        print(f"[DEBUG] converted DataFrame to {len(records)} records")
    elif isinstance(raw, (list, tuple)):
        records = list(raw)
        print(f"[DEBUG] raw is list/tuple with {len(records)} items")
    elif isinstance(raw, dict):
        records = []
        for code_key, val in raw.items():
            if isinstance(val, dict):
                entry = dict(val)
                entry.setdefault("code", code_key)
                records.append(entry)
            else:
                records.append({"code": code_key, "value": val})
        print(f"[DEBUG] converted dict to {len(records)} records")
    else:
        records = []
        print(f"[DEBUG] unknown type, using empty records")

    rows = []
    for idx, item in enumerate(records):
        print(f"[DEBUG] processing record {idx}: type={type(item)}, hasattr m_strInstrumentID={hasattr(item, 'm_strInstrumentID')}")
        # ✅ 修复：iQuant 返回的是对象，需要用 getattr 访问属性
        if hasattr(item, 'm_strInstrumentID'):
            # 对象属性方式（iQuant position 对象）
            code = getattr(item, 'm_strInstrumentID', None)
            position = getattr(item, 'm_nVolume', None)
            available = getattr(item, 'm_nCanUseVolume', None)
            cost = getattr(item, 'm_dOpenPrice', None)
            last = getattr(item, 'm_dLastPrice', None) or getattr(item, 'm_dSettlementPrice', None)
            print(f"[DEBUG]   extracted (object): code={code}, position={position}, available={available}, cost={cost}, last={last}")
        else:
            # 字典方式（兼容其他可能的返回格式）
            code = item.get("code") or item.get("stock") or item.get("symbol") or item.get("m_strInstrumentID")
            position = item.get("position") or item.get("volume") or item.get("qty") or item.get("amount") or item.get("m_nVolume")
            available = item.get("available") or item.get("enable_amount") or item.get("enable_volume") or item.get("m_nCanUseVolume")
            cost = item.get("cost_price") or item.get("avg_price") or item.get("price") or item.get("m_dOpenPrice")
            last = item.get("last") or item.get("last_price") or item.get("m_dLastPrice")
            print(f"[DEBUG]   extracted (dict): code={code}, position={position}, available={available}, cost={cost}, last={last}")

        if code is None or position is None:
            print(f"[DEBUG]   SKIPPED: code or position is None")
            continue

        rows.append(
            {
                "code": _normalize_code(code),
                "position": float(position) if position is not None else 0,
                "available": float(available) if available is not None else "",
                "cost_price": float(cost) if cost is not None else "",
                "last_price": float(last) if last is not None else "",
            }
        )

    print(f"[DEBUG] _convert_positions: converted {len(rows)} rows")
    df = pd.DataFrame(rows)
    print(f"[DEBUG] resulting DataFrame: shape={df.shape}, columns={list(df.columns) if not df.empty else 'empty'}")
    return df


def _export_positions(ContextInfo, version: str) -> bool:
    data = _fetch_positions(ContextInfo)
    if not data:
        if os.path.isfile(POSITIONS_CSV_ABS_PATH):
            print("[INFO] holdings file already exists, treated as ready")
            return True
        print("[WARN] cannot fetch holdings; please implement ContextInfo API or manually create positions_live.csv")
        return False
    df = _convert_positions(data)
    if df.empty:
        print("[WARN] holdings dataframe empty")
        return False
    _ensure_parent(POSITIONS_CSV_ABS_PATH)
    df.to_csv(POSITIONS_CSV_ABS_PATH, index=False, encoding="utf-8-sig")
    print(f"[INFO] 导出持仓 {len(df)} 条 -> {POSITIONS_CSV_ABS_PATH}")
    print(f"[DEBUG] positions preview:\n{df.head()}")
    return True


def _load_symbols() -> list:
    print(f"[DEBUG] _load_symbols: 读取文件 {SYMBOLS_REQ_ABS_PATH}")
    if not SYMBOLS_REQ_ABS_PATH or not os.path.isfile(SYMBOLS_REQ_ABS_PATH):
        print(f"[DEBUG] symbols_req 文件不存在")
        return []
    try:
        df = pd.read_csv(SYMBOLS_REQ_ABS_PATH, encoding="utf-8-sig")
        print(f"[DEBUG] symbols_req 读取成功，行数: {len(df)}, 列: {list(df.columns)}")
        col = "instrument" if "instrument" in df.columns else df.columns[0]
        symbols = [str(c).strip() for c in df[col].dropna().tolist()]
        print(f"[DEBUG] 提取到 {len(symbols)} 个股票代码")
        print(f"[DEBUG] 前5个代码: {symbols[:5]}")
        return symbols
    except Exception as err:
        print(f"[WARN] cannot read symbols_req: {err}")
        import traceback
        traceback.print_exc()
        return []


def _fetch_quotes(ContextInfo, symbols):
    """
    ✅ 修复：使用 ContextInfo.get_full_tick() 获取实时行情
    """
    print(f"[DEBUG] _fetch_quotes: 尝试获取 {len(symbols)} 个股票的行情")
    print(f"[DEBUG] 股票列表: {symbols}")

    normalized = []
    for code in symbols:
        ncode = _normalize_code(code)
        if ncode:
            normalized.append(ncode)

    if not normalized:
        print("[WARN] 正常化后的股票代码为空，无法获取行情")
        return None

    print(f"[DEBUG] 规范化后的股票列表: {normalized}")

    try:
        # 优先使用 get_full_tick
        print(f"[DEBUG] 调用 ContextInfo.get_full_tick(symbols)...")
        data = ContextInfo.get_full_tick(normalized)
        print(f"[DEBUG] get_full_tick 返回值类型: {type(data)}")
        print(f"[DEBUG] get_full_tick 返回值: {data}")

        if data:
            print(f"[DEBUG] fetch quotes via get_full_tick, count={len(data)}")
            return data
        else:
            print(f"[WARN] get_full_tick 返回空数据")
    except NameError as err:
        print(f"[ERROR] get_full_tick not found: {err}")
    except Exception as err:
        print(f"[WARN] get_full_tick failed: {err}")
        import traceback
        traceback.print_exc()

    return None


def _convert_quotes(raw, symbols, context_info=None):
    """
    ✅ 修复：正确处理 iQuant get_full_tick 返回的字典结构
    """
    print(f"[DEBUG] _convert_quotes: raw type={type(raw)}, symbols count={len(symbols)}")
    if not raw:
        print("[DEBUG] raw 为空，返回空 DataFrame")
        return pd.DataFrame()

    # get_full_tick 返回 {code: {data}} 格式
    if isinstance(raw, dict):
        print(f"[DEBUG] raw 是字典，包含 {len(raw)} 个键")
        print(f"[DEBUG] raw 的键: {list(raw.keys())[:5]}")
        rows = []
        for idx, (code, tick_data) in enumerate(raw.items()):
            if idx == 0:
                print(f"[DEBUG] 第一个 tick_data 示例: code={code}, type={type(tick_data)}, data={tick_data}")
            if not isinstance(tick_data, dict):
                print(f"[DEBUG] 跳过非字典的 tick_data: code={code}, type={type(tick_data)}")
                continue

            ncode = _normalize_code(code)

            # 从 tick_data 中提取字段
            last = tick_data.get("lastPrice") or tick_data.get("last") or tick_data.get("price")

            # 买卖盘口
            bid_arr = tick_data.get("bidPrice", [])
            ask_arr = tick_data.get("askPrice", [])
            bid1 = bid_arr[0] if isinstance(bid_arr, list) and len(bid_arr) > 0 else None
            ask1 = ask_arr[0] if isinstance(ask_arr, list) and len(ask_arr) > 0 else None

            # 涨跌停价格（从 get_full_tick 无法获取，需要另外调用 get_instrumentdetail）
            high_limit = tick_data.get("high_limit") or tick_data.get("UpStopPrice")
            low_limit = tick_data.get("low_limit") or tick_data.get("DownStopPrice")

            # 如果没有涨跌停价格，尝试从 get_instrumentdetail 获取
            if (high_limit is None or low_limit is None) and context_info and hasattr(context_info, 'get_instrumentdetail'):
                try:
                    detail = context_info.get_instrumentdetail(ncode)
                    if detail:
                        high_limit = detail.get('UpStopPrice')
                        low_limit = detail.get('DownStopPrice')
                except:
                    pass

            rows.append({
                "code": ncode,
                "last": float(last) if last is not None else "",
                "bid1": float(bid1) if bid1 is not None else "",
                "ask1": float(ask1) if ask1 is not None else "",
                "high_limit": float(high_limit) if high_limit is not None else "",
                "low_limit": float(low_limit) if low_limit is not None else "",
            })

        print(f"[DEBUG] 转换完成，生成 {len(rows)} 行数据")
        df = pd.DataFrame(rows)
        print(f"[DEBUG] DataFrame shape={df.shape}, columns={list(df.columns) if not df.empty else 'empty'}")
        return df

    # 兼容其他格式
    print(f"[DEBUG] raw 不是字典类型，返回空 DataFrame")
    return pd.DataFrame()


def _create_mock_quotes(symbols):
    """创建模拟行情数据（用于非交易时间测试）"""
    import random
    rows = []
    for code in symbols:
        ncode = _normalize_code(code)
        # 生成模拟价格（10-50元之间）
        base_price = random.uniform(10.0, 50.0)
        rows.append({
            "code": ncode,
            "last": base_price,
            "bid1": base_price * 0.999,  # 买一价略低
            "ask1": base_price * 1.001,  # 卖一价略高
            "high_limit": base_price * 1.10,  # 涨停价
            "low_limit": base_price * 0.90,   # 跌停价
        })
    print(f"[INFO] 创建了 {len(rows)} 条模拟行情数据")
    return pd.DataFrame(rows)


def _export_quotes(ContextInfo, version: str) -> bool:
    symbols = _load_symbols()
    if not symbols:
        print("[WARN] symbols_req.csv is empty or missing")
        return False
    data = _fetch_quotes(ContextInfo, symbols)
    if not data:
        if os.path.isfile(QUOTES_CSV_ABS_PATH):
            print("[INFO] quotes file already exists, treated as ready")
            return True
        # 非交易时间，创建模拟行情数据
        print("[WARN] get_full_tick 返回空数据（可能非交易时间），创建模拟行情...")
        df = _create_mock_quotes(symbols)
    else:
        df = _convert_quotes(data or {}, symbols, context_info=ContextInfo)

    if df.empty:
        print("[WARN] quotes dataframe empty")
        return False
    _ensure_parent(QUOTES_CSV_ABS_PATH)
    df.to_csv(QUOTES_CSV_ABS_PATH, index=False, encoding="utf-8-sig")
    print(f"[INFO] 导出行情 {len(df)} 条 -> {QUOTES_CSV_ABS_PATH}")
    print(f"[DEBUG] quotes preview:\n{df.head()}")
    return True


# ---------- 主动轮询处理函数 ----------
def _process_state(ContextInfo):
    """处理当前 state，返回 True 表示需要继续轮询，False 表示已完成"""
    global _ERROR_ONCE, _FIRED

    state = _read_state()
    phase = state.get("phase")
    version = state.get("version")
    expect_ver = TARGET_VERSION or version

    print(f"[DEBUG] _process_state: phase={phase}, version={version}")

    # 如果 state.json 不存在或 phase 为空，继续等待
    if not phase:
        print("[iQuant][INFO] state.json 不存在或 phase 为空，继续等待...")
        return True  # 继续轮询

    if phase == "positions_needed":
        print(f"[iQuant] 检测到 phase=positions_needed，开始导出持仓...")
        if _export_positions(ContextInfo, expect_ver or ""):
            _write_state("positions_ready", expect_ver or "", {"source": "iquant"})
            print(f"[iQuant] 持仓导出完成，已写入 positions_ready")
        else:
            print("[INFO] 持仓导出失败，等待手动导出")
        return True  # 继续轮询

    if phase == "symbols_ready":
        print(f"[iQuant] 检测到 phase=symbols_ready，开始导出行情...")
        if _export_quotes(ContextInfo, expect_ver or ""):
            _write_state("quotes_ready", expect_ver or "", {"source": "iquant"})
            print(f"[iQuant] 行情导出完成，已写入 quotes_ready")
        else:
            print("[INFO] 行情导出失败，等待手动导出")
        return True  # 继续轮询

    if phase == "orders_ready":
        if TARGET_VERSION and version != TARGET_VERSION:
            print(f"[iQuant][INFO] state.version={version} 不匹配 {TARGET_VERSION}，跳过")
            return False  # 停止轮询
        if _FIRED:
            print("[DEBUG] 已执行过，停止轮询")
            return False  # 停止轮询

        try:
            print(f"[iQuant] 检测到 phase=orders_ready，开始执行订单...")

            # ========== 账户和环境验证 ==========
            import datetime
            print(f"[DEBUG][orders] 当前时间: {datetime.datetime.now()}")
            print(f"[DEBUG][orders] ContextInfo.accid: {getattr(ContextInfo, 'accid', 'NOT SET')}")
            print(f"[DEBUG][orders] ContextInfo 类型: {type(ContextInfo)}")
            print(f"[DEBUG][orders] ACCOUNT_ID: {ACCOUNT_ID}")
            print(f"[DEBUG][orders] ACCOUNT_TYPE: {ACCOUNT_TYPE}")

            # 验证账户是否可访问
            acc_id = getattr(ContextInfo, "accid", None) or ACCOUNT_ID
            if not acc_id:
                print(f"[ERROR][orders] 账户 ID 未设置！")
            else:
                print(f"[DEBUG][orders] 使用账户 ID: {acc_id}")
                # 尝试查询持仓验证账户
                try:
                    print(f"[DEBUG][orders] 尝试查询账户持仓以验证账户...")
                    test_positions = get_trade_detail_data(acc_id, ACCOUNT_TYPE, "position")
                    if test_positions:
                        print(f"[DEBUG][orders] ✅ 账户验证成功，当前持仓 {len(test_positions)} 条")
                    else:
                        print(f"[WARN][orders] 账户查询返回空（可能正常）")
                except Exception as verify_err:
                    print(f"[WARN][orders] 账户验证失败: {verify_err}")

            df = getattr(ContextInfo, "_qlib_df", None)
            if df is None:
                df = _load_orders()
            _print_overview(df)
            _place_orders(df, ContextInfo, expect_ver or "")
            _FIRED = True
            _write_state("exec_done", expect_ver or "", {"status": "ok"})
            print("[iQuant] 订单执行完毕，流程结束")
            return False  # 停止轮询
        except Exception as exc:
            if not _ERROR_ONCE:
                print(f"[iQuant][ERROR] 执行失败: {exc}")
                import traceback
                traceback.print_exc()
                _ERROR_ONCE = True
            _write_state("exec_failed", expect_ver or "", {"error": str(exc)})
            return False  # 停止轮询

    # 其他 phase（positions_ready, quotes_ready, exec_done, exec_failed 等）
    print(f"[iQuant][INFO] 当前 phase={phase}，继续等待...")
    return True  # 继续轮询


# ---------- QMT lifecycle ----------
def init(ContextInfo):
    """策略加载后调用一次：设置账户并提前缓存 CSV"""
    if ACCOUNT_ID:
        ContextInfo.accid = ACCOUNT_ID
        # ❌ 移除 set_account() 调用，与成功案例保持一致
        print(f"[DEBUG][init] 设置账户 ID: {ACCOUNT_ID}")
    else:
        print(f"[WARN][init] ACCOUNT_ID 未配置")

    print(f"[DEBUG][init] ContextInfo.accid = {getattr(ContextInfo, 'accid', 'NOT SET')}")

    ContextInfo._qlib_df = None
    ContextInfo._polling_started = False
    try:
        ContextInfo._qlib_df = _load_orders()
        print(f"[DEBUG][init] 预加载订单 CSV 成功")
    except Exception as exc:
        print(f"[iQuant][ERROR] init load orders failed: {exc}")
        ContextInfo._qlib_df = None


def handlebar(ContextInfo):
    """每个 bar 调用一次，首次调用时启动主动轮询"""
    # ========== 关键 DEBUG：bar 状态诊断 ==========
    ts_func = getattr(ContextInfo, 'get_bar_timetag', None)
    ts = ts_func(ContextInfo.barpos) if callable(ts_func) else None
    is_last_bar_func = getattr(ContextInfo, 'is_last_bar', lambda: True)
    is_last = is_last_bar_func()

    print(f"[DEBUG][handlebar] barpos={getattr(ContextInfo, 'barpos', 'N/A')}, "
          f"timestamp={ts}, is_last_bar={is_last}, accid={getattr(ContextInfo, 'accid', 'NOT SET')}")

    # ✅ 关键检查：只在实时 bar 执行，防止在历史回放阶段下单
    if not is_last:
        print(f"[DEBUG][handlebar] 非实时 bar，跳过执行")
        return

    print(f"[DEBUG][handlebar] ✅ 确认为实时 bar，继续执行")

    # 只在首次调用时启动轮询
    if getattr(ContextInfo, "_polling_started", False):
        print(f"[DEBUG][handlebar] 轮询已启动，跳过")
        return

    ContextInfo._polling_started = True
    print("[iQuant] ========== 开始主动轮询 state.json ==========")
    print(f"[iQuant] 轮询间隔: {POLL_INTERVAL} 秒，最大轮询次数: {MAX_POLL_COUNT}")

    poll_count = 0
    while poll_count < MAX_POLL_COUNT:
        poll_count += 1
        print(f"\n[iQuant] ===== 第 {poll_count} 次轮询 =====")

        # 处理当前 state
        should_continue = _process_state(ContextInfo)

        if not should_continue:
            print("[iQuant] 流程完成，停止轮询")
            break

        # 等待下一次轮询
        print(f"[iQuant] 等待 {POLL_INTERVAL} 秒后继续...")
        time.sleep(POLL_INTERVAL)

    if poll_count >= MAX_POLL_COUNT:
        print(f"[iQuant][WARN] 达到最大轮询次数 {MAX_POLL_COUNT}，停止轮询")

    print("[iQuant] ========== 轮询结束 ==========")


# ---------- 订单状态回调函数 ----------
def orderError_callback(ContextInfo, orderArgs, errMsg):
    """
    下单异常时的回调函数
    当 passorder 等下单函数执行失败时，iQuant 会自动调用此函数
    """
    print("\n" + "="*60)
    print("[iQuant][订单异常] 下单失败！")
    print(f"[iQuant][订单异常] 错误信息: {errMsg}")
    print(f"[iQuant][订单异常] 订单参数: {orderArgs}")
    if hasattr(orderArgs, '__dict__'):
        print(f"[iQuant][订单异常] 订单详情: {orderArgs.__dict__}")
    print("="*60 + "\n")


def order_callback(ContextInfo, orderInfo):
    """
    委托状态变化时的回调函数
    当订单状态有变化时（如已报、已成、已撤等），iQuant 会自动调用此函数
    """
    print("\n" + "-"*60)
    print("[iQuant][委托回调] 委托状态变化")
    if hasattr(orderInfo, 'm_strInstrumentID'):
        code = getattr(orderInfo, 'm_strInstrumentID', 'N/A')
        status = getattr(orderInfo, 'm_nOrderStatus', 'N/A')  # 委托状态
        msg = getattr(orderInfo, 'm_strStatusMsg', '')  # 状态消息
        order_id = getattr(orderInfo, 'm_strOrderSysID', 'N/A')  # 委托号
        remark = getattr(orderInfo, 'm_strRemark', '')  # 用户自定义ID (userOrderId)
        print(f"[iQuant][委托回调] 股票代码: {code}")
        print(f"[iQuant][委托回调] 委托号: {order_id}")
        print(f"[iQuant][委托回调] 用户ID: {remark}")
        print(f"[iQuant][委托回调] 委托状态: {status} - {msg}")
    else:
        print(f"[iQuant][委托回调] 委托信息: {orderInfo}")
    print("-"*60 + "\n")


def deal_callback(ContextInfo, dealInfo):
    """
    成交状态变化时的回调函数
    当订单有成交时，iQuant 会自动调用此函数
    """
    print("\n" + "+"*60)
    print("[iQuant][成交回调] 订单成交！")
    if hasattr(dealInfo, 'm_strInstrumentID'):
        code = getattr(dealInfo, 'm_strInstrumentID', 'N/A')
        price = getattr(dealInfo, 'm_dPrice', 'N/A')  # 成交价格
        volume = getattr(dealInfo, 'm_nVolume', 'N/A')  # 成交数量
        order_id = getattr(dealInfo, 'm_strOrderSysID', 'N/A')  # 委托号
        remark = getattr(dealInfo, 'm_strRemark', '')  # 用户自定义ID
        print(f"[iQuant][成交回调] 股票代码: {code}")
        print(f"[iQuant][成交回调] 委托号: {order_id}")
        print(f"[iQuant][成交回调] 用户ID: {remark}")
        print(f"[iQuant][成交回调] 成交价格: {price}")
        print(f"[iQuant][成交回调] 成交数量: {volume}")
    else:
        print(f"[iQuant][成交回调] 成交信息: {dealInfo}")
    print("+"*60 + "\n")

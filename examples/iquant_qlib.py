# coding: gbk
# iQuant-side script handling three handshake phases with qlib:
#   1) positions_needed -> export holdings -> positions_ready
#   2) symbols_ready    -> export live quotes -> quotes_ready
#   3) orders_ready     -> execute orders -> exec_done/exec_failed

import os
import json
import pandas as pd

# ===== Paths (edit to your environment) =====
POSITIONS_CSV_ABS_PATH = r"D:\code\qlib\qlib\predictions\positions_live.csv"
QUOTES_CSV_ABS_PATH = r"D:\code\qlib\qlib\predictions\quotes_live.csv"
SYMBOLS_REQ_ABS_PATH = r"D:\code\qlib\qlib\predictions\symbols_req.csv"
ORDERS_CSV_ABS_PATH = r"D:\code\qlib\qlib\predictions\orders_to_exec.csv"
STATE_JSON_PATH = r"D:\code\qlib\qlib\predictions\state.json"
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
    if not STATE_JSON_PATH or not os.path.isfile(STATE_JSON_PATH):
        return {}
    try:
        with open(STATE_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        print("[DEBUG] state =", data)
        return data
    except Exception:
        print("[WARN] state.json unreadable")
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


def _call_order_shares(code, qty, price, ContextInfo):
    acc_id = getattr(ContextInfo, "accid", None) or (ACCOUNT_ID or None)
    is_sell = qty < 0
    opType = 24 if is_sell else 23
    volume = abs(qty)
    if USE_LIMIT_PRICE and price is not None and price > 0:
        prType = 0
        use_price = float(price)
    else:
        prType = 4
        use_price = -1
    print(f"[DEBUG] order code={code}, qty={qty}, opType={opType}, prType={prType}, price={use_price}, accid={acc_id}")
    ret = passorder(opType, 1101, acc_id, code, prType, use_price, volume, 'qlib_batch', 1, '', ContextInfo)
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
            result = _call_order_shares(code, final_qty, price, ContextInfo)
        except Exception as err:
            warn = str(err)
        else:
            warn = "" if result in (None, 0) else _extract_order_error(result)

        if warn:
            print(f"[iQuant][WARN] {code} {side} {abs(final_qty)} 股 未成交: {warn} (order_id={oid}, ver={version})")
        else:
            print(f"[iQuant] 已提交 {code} {side} {abs(final_qty)} 股 (order_id={oid}, ver={version})")
        _DONE_IDS.add(oid)


# ---------- export helpers ----------
def _fetch_positions(ContextInfo):
    """
    ✅ 修复：正确调用 iQuant 的 get_trade_detail_data 获取持仓
    """
    acc_id = ACCOUNT_ID or getattr(ContextInfo, "accid", None)
    if not acc_id:
        print("[WARN] ACCOUNT_ID is not set")
        return None

    try:
        # ✅ 修复：第三个参数必须是小写的 "position"
        if POSITION_STRATEGY_NAME:
            data = get_trade_detail_data(acc_id, ACCOUNT_TYPE, "position", POSITION_STRATEGY_NAME)
        else:
            data = get_trade_detail_data(acc_id, ACCOUNT_TYPE, "position")

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
    if isinstance(raw, pd.DataFrame):
        records = raw.to_dict("records")
    elif isinstance(raw, (list, tuple)):
        records = list(raw)
    elif isinstance(raw, dict):
        records = []
        for code_key, val in raw.items():
            if isinstance(val, dict):
                entry = dict(val)
                entry.setdefault("code", code_key)
                records.append(entry)
            else:
                records.append({"code": code_key, "value": val})
    else:
        records = []

    rows = []
    for item in records:
        # ✅ 修复：iQuant 返回的是对象，需要用 getattr 访问属性
        if hasattr(item, 'm_strInstrumentID'):
            # 对象属性方式（iQuant position 对象）
            code = getattr(item, 'm_strInstrumentID', None)
            position = getattr(item, 'm_nVolume', None)
            available = getattr(item, 'm_nCanUseVolume', None)
            cost = getattr(item, 'm_dOpenPrice', None)
            last = getattr(item, 'm_dLastPrice', None) or getattr(item, 'm_dSettlementPrice', None)
        else:
            # 字典方式（兼容其他可能的返回格式）
            code = item.get("code") or item.get("stock") or item.get("symbol") or item.get("m_strInstrumentID")
            position = item.get("position") or item.get("volume") or item.get("qty") or item.get("amount") or item.get("m_nVolume")
            available = item.get("available") or item.get("enable_amount") or item.get("enable_volume") or item.get("m_nCanUseVolume")
            cost = item.get("cost_price") or item.get("avg_price") or item.get("price") or item.get("m_dOpenPrice")
            last = item.get("last") or item.get("last_price") or item.get("m_dLastPrice")

        if code is None or position is None:
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

    return pd.DataFrame(rows)


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
    if not SYMBOLS_REQ_ABS_PATH or not os.path.isfile(SYMBOLS_REQ_ABS_PATH):
        return []
    try:
        df = pd.read_csv(SYMBOLS_REQ_ABS_PATH, encoding="utf-8-sig")
        col = "instrument" if "instrument" in df.columns else df.columns[0]
        return [str(c).strip() for c in df[col].dropna().tolist()]
    except Exception as err:
        print(f"[WARN] cannot read symbols_req: {err}")
        return []


def _fetch_quotes(ContextInfo, symbols):
    """
    ✅ 修复：使用 ContextInfo.get_full_tick() 获取实时行情
    """
    try:
        # 优先使用 get_full_tick
        data = ContextInfo.get_full_tick(symbols)
        if data:
            print(f"[DEBUG] fetch quotes via get_full_tick, count={len(data)}")
            return data
    except NameError as err:
        print(f"[ERROR] get_full_tick not found: {err}")
    except Exception as err:
        print(f"[WARN] get_full_tick failed: {err}")
        import traceback
        traceback.print_exc()

    return None


def _convert_quotes(raw, symbols):
    """
    ✅ 修复：正确处理 iQuant get_full_tick 返回的字典结构
    """
    if not raw:
        return pd.DataFrame()

    # get_full_tick 返回 {code: {data}} 格式
    if isinstance(raw, dict):
        rows = []
        for code, tick_data in raw.items():
            if not isinstance(tick_data, dict):
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
            if (high_limit is None or low_limit is None) and hasattr(ContextInfo, 'get_instrumentdetail'):
                try:
                    detail = ContextInfo.get_instrumentdetail(ncode)
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

        return pd.DataFrame(rows)

    # 兼容其他格式
    return pd.DataFrame()


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
    df = _convert_quotes(data or {}, symbols)
    if df.empty:
        print("[WARN] quotes dataframe empty")
        return False
    _ensure_parent(QUOTES_CSV_ABS_PATH)
    df.to_csv(QUOTES_CSV_ABS_PATH, index=False, encoding="utf-8-sig")
    print(f"[INFO] 导出行情 {len(df)} 条 -> {QUOTES_CSV_ABS_PATH}")
    print(f"[DEBUG] quotes preview:\n{df.head()}")
    return True


# ---------- QMT lifecycle ----------
def init(ContextInfo):
    if ACCOUNT_ID:
        ContextInfo.accid = ACCOUNT_ID
    ContextInfo._qlib_df = None
    try:
        ContextInfo._qlib_df = _load_orders()
    except Exception as exc:
        print(f"[iQuant][ERROR] init load orders failed: {exc}")
        ContextInfo._qlib_df = None


def handlebar(ContextInfo):
    global _ERROR_ONCE, _FIRED
    ts_func = getattr(ContextInfo, "get_bar_timetag", None)
    ts = ts_func(ContextInfo.barpos) if callable(ts_func) else None
    print(f"[DEBUG] handlebar accid={getattr(ContextInfo, 'accid', None)}, barpos={getattr(ContextInfo, 'barpos', None)}, ts={ts}")

    is_last = getattr(ContextInfo, "is_last_bar", lambda: True)
    if not is_last():
        print("[DEBUG] 非实时 bar，等待最后一根")
        return

    state = _read_state()
    phase = state.get("phase")
    version = state.get("version")
    expect_ver = TARGET_VERSION or version

    if phase == "positions_needed":
        if _export_positions(ContextInfo, expect_ver or ""):
            _write_state("positions_ready", expect_ver or "", {"source": "iquant"})
        else:
            print("[INFO] 等待手动导出持仓")
        return

    if phase == "symbols_ready":
        if _export_quotes(ContextInfo, expect_ver or ""):
            _write_state("quotes_ready", expect_ver or "", {"source": "iquant"})
        else:
            print("[INFO] 等待手动导出行情")
        return

    if phase != "orders_ready":
        print(f"[iQuant][INFO] state.phase={phase}，未到 orders_ready，跳过")
        return
    if TARGET_VERSION and version != TARGET_VERSION:
        print(f"[iQuant][INFO] state.version={version} 不匹配 {TARGET_VERSION}，跳过")
        return
    if _FIRED:
        print("[DEBUG] 已执行过，本次跳过")
        return

    try:
        df = getattr(ContextInfo, "_qlib_df", None)
        if df is None:
            df = _load_orders()
        _print_overview(df)
        _place_orders(df, ContextInfo, expect_ver or "")
        _FIRED = True
        _write_state("exec_done", expect_ver or "", {"status": "ok"})
        print("[DEBUG] 下单执行完毕")
    except Exception as exc:
        if not _ERROR_ONCE:
            print(f"[iQuant][ERROR] 执行失败: {exc}")
            import traceback
            traceback.print_exc()
            _ERROR_ONCE = True
        _write_state("exec_failed", expect_ver or "", {"error": str(exc)})

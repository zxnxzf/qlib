# -*- coding: gbk -*-
"""基于 CSV 的 iQuant/QMT 批量下单脚本。"""

import os
import pandas as pd

# ===== CSV 路径配置 =====
CSV_ABS_PATH = r"D:\code\qlib\qlib\predictions\trading_orders_20251112.csv"
# =======================

# ---------- 运行参数 ----------
DRY_RUN = False            # True=仅打印计划，False=真实下单
ENFORCE_BUY_100_LOT = True   # 买入数量是否按 100 股向下取整
ROUND_SELL_TO_100 = False    # 卖出是否也按 100 股取整
ACCOUNT_ID = "410015004039"  # 可选：指定账户，留空则沿用 ContextInfo.accid

PRINT_HEAD_ROWS = 10

_DF_CACHE = None
_ERROR_ONCE = False


# --- 工具函数 ------------------------------------------------------------
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


def _csv_path() -> str:
    if CSV_ABS_PATH and os.path.isabs(CSV_ABS_PATH):
        return CSV_ABS_PATH
    raise ValueError("请配置合法的 CSV 绝对路径")


def _load_df() -> pd.DataFrame:
    global _DF_CACHE
    if _DF_CACHE is not None:
        return _DF_CACHE

    path = _csv_path()
    if not os.path.isfile(path):
        raise FileNotFoundError(f"找不到 CSV 文件: {path}")

    df = pd.read_csv(path, encoding="utf-8-sig", skipinitialspace=True)
    df.columns = [str(c).strip().lower() for c in df.columns]

    required = {"order_id", "stock", "action", "shares"}
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"CSV 缺少字段: {missing}")

    df = df.rename(columns={"stock": "code"})
    df["code"] = df["code"].map(_normalize_code)
    df["order_id"] = df["order_id"].astype(str).str.strip()
    df["action"] = df["action"].astype(str).str.strip()

    df["shares"] = pd.to_numeric(df["shares"], errors="coerce").fillna(0).astype(int)
    if "price" in df.columns:
        df["price"] = pd.to_numeric(df.get("price"), errors="coerce")

    df = df[(df["code"] != "") & (df["shares"] != 0)].copy()

    _DF_CACHE = df
    return _DF_CACHE


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
    """尽量解析 order_shares 返回结果，生成可读的提示。"""
    if ret is None:
        return "order_shares 返回 None"
    for attr in ("m_nErrorCode", "error_code", "ErrorID"):
        code = getattr(ret, attr, None)
        if code not in (None, 0):
            msg = getattr(ret, "m_strErrorMsg", getattr(ret, "error_msg", getattr(ret, "ErrorMsg", ""))) or "unknown"
            return f"error_code={code}, msg={msg}"
    if isinstance(ret, bool):
        return "" if ret else "order_shares 返回 False"
    if isinstance(ret, (int, float)):
        return "" if ret > 0 else f"order_shares 返回 {ret}"
    if isinstance(ret, (tuple, list)) and ret:
        status = ret[0]
        if isinstance(status, (int, float)) and status <= 0:
            return f"order_shares tuple status={status}"
    return ""


def _call_order_shares(code, qty, ContextInfo):
    """使用 passorder 下单"""
    acc_id = getattr(ContextInfo, "accid", None) or (ACCOUNT_ID or None)
    print(f"[DEBUG] 下单参数: code={code}, qty={qty}, accid={acc_id}")

    is_sell = qty < 0
    opType = 24 if is_sell else 23
    volume = abs(qty)
    prType = 5

    ret = passorder(opType, 1101, acc_id, code, prType, -1, volume, 'qlib_batch', 1, '', ContextInfo)
    return ret


# --- 打印辅助 ------------------------------------------------------------
def _print_overview(df: pd.DataFrame):
    print("[iQuant] ===== CSV 摘要 =====")
    print("[iQuant] 文件:", _csv_path())
    print("[iQuant] 列:", list(df.columns))
    print(f"[iQuant] 前 {PRINT_HEAD_ROWS} 行:")
    print(df.head(PRINT_HEAD_ROWS).to_string(index=False))
    print("[iQuant] 总行数:", len(df))
    print("[iQuant] ===== 摘要结束 =====")


# --- 核心逻辑 ------------------------------------------------------------
def _place_orders_by_shares(df: pd.DataFrame, ContextInfo):
    for _, row in df.iterrows():
        code = str(row["code"])
        raw_qty = int(row["shares"])
        signed_qty = _sign_by_action(row["action"], raw_qty)
        is_sell = signed_qty < 0
        final_qty = _round_shares(signed_qty, is_sell)

        if final_qty == 0:
            print(f"[iQuant][SKIP] {code} 原计划 {raw_qty} 股 -> 四舍五入后为 0，跳过。")
            continue

        side = "卖出" if final_qty < 0 else "买入"
        if DRY_RUN:
            print(f"[DRY_RUN] {code} {side} {abs(final_qty)} 股 (最新价)，仅打印不下单")
            continue

        try:
            result = _call_order_shares(code, final_qty, ContextInfo)
        except Exception as err:
            warn_msg = str(err)
        else:
            warn_msg = "" if result in (None, 0) else _extract_order_error(result)

        if warn_msg:
            print(f"[iQuant][WARN] {code} {side} {abs(final_qty)} 股可能未成交: {warn_msg}")
        else:
            print(f"[iQuant] 已提交 {code} {side} {abs(final_qty)} 股 (最新价)")


# --- QMT 生命周期 --------------------------------------------------------
def init(ContextInfo):
    """策略加载后调用一次：设置账户并提前缓存 CSV。"""
    if ACCOUNT_ID:
        ContextInfo.accid = ACCOUNT_ID
    ContextInfo._qlib_has_run = False
    ContextInfo._qlib_df = None
    try:
        ContextInfo._qlib_df = _load_df()
    except Exception as exc:
        print(f"[iQuant][ERROR] 初始化时加载 CSV 失败: {exc}")
        ContextInfo._qlib_df = None


# ===================== iQuant hook =====================
def handlebar(ContextInfo):
    """每根 bar 触发一次，只在首个 bar 执行 CSV 指令。"""
    global _ERROR_ONCE
    ts_func = getattr(ContextInfo, 'get_bar_timetag', None)
    ts = ts_func(ContextInfo.barpos) if callable(ts_func) else None
    print(f"[DEBUG] handlebar accid={getattr(ContextInfo, 'accid', None)}, barpos={getattr(ContextInfo, 'barpos', None)}, ts={ts}")
    # 只在实时 bar 触发，避免预热/历史段重复下单
    is_last = getattr(ContextInfo, 'is_last_bar', lambda: True)
    if not is_last():
        return

    if getattr(ContextInfo, "_qlib_has_run", False):
        return
    try:
        df = getattr(ContextInfo, "_qlib_df", None)
        if df is None:
            df = _load_df()
        _print_overview(df)
        _place_orders_by_shares(df, ContextInfo)
        ContextInfo._qlib_has_run = True
    except Exception as exc:
        if not _ERROR_ONCE:
            print(f"[iQuant][ERROR] 执行失败: {exc}")
            _ERROR_ONCE = True
    return

















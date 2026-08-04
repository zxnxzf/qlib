"""数据层：qlib 初始化、更新+硬校验、日历/行情读取、免费指数备源。"""

import json
import subprocess
import urllib.request
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from . import config as C

_qlib_ready = False


def init_qlib(kernels: int = 4) -> None:
    global _qlib_ready
    if _qlib_ready:
        return
    import qlib

    qlib.init(provider_uri=str(C.DATA_DIR), region="cn", kernels=kernels)
    _qlib_ready = True


def update_data() -> bool:
    """调用既有硬校验更新脚本（manifest+sha256+日历硬闸）。成功返回 True。"""
    r = subprocess.run([C.VENV_PY, C.UPDATE_SCRIPT], capture_output=True, text=True, timeout=3600)
    ok = r.returncode == 0 and "[done]" in (r.stdout + r.stderr)
    _rebuild_pool_file()      # 数据包整目录替换会抹掉自建票池文件，每次更新后重建
    return ok


def _rebuild_pool_file() -> None:
    src = C.DATA_DIR / "instruments" / "all.txt"
    dst = C.DATA_DIR / "instruments" / f"{C.POOL}.txt"
    if src.exists():
        lines = [ln for ln in src.read_text().splitlines() if not ln.startswith("BJ")]
        dst.write_text("\n".join(lines) + "\n")


def calendar() -> List[str]:
    with (C.DATA_DIR / "calendars" / "day.txt").open() as f:
        return [ln.strip() for ln in f if ln.strip()]


def future_calendar() -> List[str]:
    days = set(calendar())
    fp = C.DATA_DIR / "calendars" / "day_future.txt"
    if fp.exists():
        with fp.open() as f:
            days |= {ln.strip() for ln in f if ln.strip()}
    return sorted(days)


def latest_data_date() -> str:
    return calendar()[-1]


def next_trade_date(date: str) -> Optional[str]:
    for d in future_calendar():
        if d > date:
            return d
    return None


def expected_signal_date(now: Optional[datetime] = None) -> str:
    """返回当前这次调度应处理的最新已收盘交易日。

    18:00 以后允许处理当日；18:00 前（包括次日晨间补跑）只处理前一交易日。
    """
    now = now or datetime.now()
    cutoff = now.date()
    if now.hour < 18:
        cutoff = (pd.Timestamp(cutoff) - pd.Timedelta(days=1)).date()
    candidates = [d for d in future_calendar() if pd.Timestamp(d).date() <= cutoff]
    if not candidates:
        raise RuntimeError(f"交易日历无法确定 {now.isoformat()} 对应的信号日")
    return candidates[-1]


def day_bars(date: str, fields=("$open", "$close", "$volume", "$factor")) -> pd.DataFrame:
    """某交易日全票池行情（含前收盘），index=instrument。"""
    from qlib.data import D

    init_qlib()
    inst = D.instruments(C.POOL)
    exprs = list(fields) + ["Ref($close,1)"]
    df = D.features(inst, exprs, start_time=date, end_time=date)
    df.columns = [f.replace("$", "") for f in fields] + ["prev_close"]
    return df.droplevel(1)  # 单日：去掉 datetime 层


def index_closes(index_code: str, start: str, end: str) -> pd.Series:
    from qlib.data import D

    init_qlib()
    px = D.features([index_code], ["$close"], start_time=start, end_time=end)
    s = px.droplevel(0)["$close"]
    s.index = pd.to_datetime(s.index)
    return s


def index_close_fallback(index_code: Optional[str] = None) -> Optional[float]:
    """免费指数备源（腾讯行情 HTTP 接口），数据包断供时用于门控判定。"""
    try:
        index_code = index_code or C.GATE_INDEX
        url = f"https://qt.gtimg.cn/q={index_code.lower()}"
        raw = urllib.request.urlopen(url, timeout=10).read().decode("gbk", errors="ignore")
        parts = raw.split("~")
        return float(parts[3])  # 最新价
    except Exception:
        return None

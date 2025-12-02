#!/usr/bin/env python3  # 指定解释器
"""
live_daily_predict：实盘两阶段入口，复用 daily_predict 但支持实时报价定价。
Phase1：T-1 特征跑模型+Topk，输出候选 symbols_req。
Phase2：注入当日 quotes_live，用实时报价算 shares/price，输出 orders_to_exec。
"""

import argparse  # 解析命令行参数（可选 --config）
import json  # 读取 JSON 配置 / state.json
import shutil
import tarfile
import time  # 等待 state 用
from dataclasses import dataclass, replace  # 覆盖配置的便捷方法
from datetime import datetime  # 持仓历史日期计算
from pathlib import Path  # 路径处理
from typing import Dict, Optional  # 类型注解
from urllib.request import urlretrieve
import sys  # 修改 sys.path 便于导入

import numpy as np  # 数值计算
import pandas as pd  # 数据处理
from pandas.tseries.offsets import BDay

# 确保优先使用项目根目录下的 qlib 包，避免被 examples/qlib 命名空间遮蔽
_EXAMPLES_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _EXAMPLES_DIR.parent
for _path in (str(_REPO_ROOT), str(_EXAMPLES_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from qlib.utils import get_pre_trading_date  # 获取前一交易日
from qlib.backtest.decision import OrderDir  # 订单方向枚举
from qlib.backtest.live_exchange import LiveExchange  # 实时报价 Exchange
from qlib.data import D

# 从同级 daily_predict 导入原有配置和 Pipeline（路径已在上方插入）
from daily_predict import (  # noqa: E402  # 延迟导入
    DailyPredictionPipeline,
    PredictionConfig,
    TradingConfig,
    OutputConfig,
)


# ================= 用户可直接在此处配置缺省参数 =================
DEFAULT_CONFIG = {
    "qlib_init": {
        "provider_uri": "~/.qlib/qlib_data/cn_data",  # 数据路径
        "region": "cn",  # 市场区域
        "kernels": 1,  # 进程池个数
        "joblib_backend": "threading",  # joblib 后端类型
        "maxtasksperchild": 1,  # 子进程最大任务数
    },
    "paths": {
        "positions": "predictions/positions_live.csv",  # 持仓 CSV
        "quotes": "predictions/quotes_live.csv",  # 实时报价 CSV
        "symbols_out": "predictions/symbols_req.csv",  # Phase1 输出路径
        "orders_out": "predictions/orders_to_exec.csv",  # Phase2 输出路径
        "state": "predictions/state.json",  # 握手状态文件
    },
    "runtime": {
        "version": None,  # 运行批次号
        "wait_secs": 300,  # 等待下一阶段超时时间
    },
    "prediction": {
        "experiment_id": "866149675032491302",  # 实验 ID
        "recorder_id": "3d0e78192f384bb881c63b32f743d5f8",  # recorder ID
        "prediction_date": "auto",  # 预测日期（auto=最新交易日）
        "top_k": 20,  # 选股数量
        "min_score_threshold": 0.0,  # 评分阈值
        "weight_method": "equal",  # 权重计算方式
        "provider_uri": "~/.qlib/qlib_data/cn_data",  # 数据源
        "region": "cn",  # 区域
        "instruments": "csi300",  # 证券池
        "dataset_class": "DatasetH",  # 数据集类型
        "dataset_module": "qlib.data.dataset",  # 数据集模块
        "handler_class": "Alpha158",  # Handler 类
        "handler_module": "qlib.contrib.data.handler",  # Handler 模块
        "min_history_days": 120,  # 最短历史窗口
    },
    "trading": {
        "enable_trading": True,  # 是否生成交易
        "use_exchange_system": True,  # 是否使用 Exchange 系统
        "total_cash": 50000,  # 资金规模
        "min_shares": 100,  # 单笔最小股数
        "price_search_days": 5,  # 回溯价格天数
        "risk_degree": 0.0001,  # 可用资金比例，账户有20万 * 10 ^ 3，预计使用20万
        "n_drop": 3,  # TopkDropoutStrategy: 每次替换的股票数量（Dropout 换仓机制）
        "hold_thresh": 1,  # TopkDropoutStrategy: 最短持有天数（持有天数控制，单位：交易日）
        # LiveTopkStrategy 相关配置
        "use_live_topk_strategy": True,  # 是否使用 LiveTopkStrategy（小资金优化）
        "min_affordable_shares": 100,  # LiveTopkStrategy: 最小可负担股数（默认1手）
        # 交易成本参数（符合中国 A 股实际成本）
        "open_cost": 0.0003,   # 买入成本率（佣金 0.025% + 过户费 0.001%）
        "close_cost": 0.0013,  # 卖出成本率（佣金 0.025% + 印花税 0.1% + 过户费 0.001%）
        "min_cost": 1.0,       # 最小交易成本（元）
        "impact_cost": 0.0,    # 市场冲击成本/滑点
    },
    "data_update": {
        "enable_auto_update": True,  # 是否自动更新数据
        "data_source_url": "https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz",  # 数据源 URL
        "download_timeout": 600,  # 下载超时
        "temp_dir": None,  # 临时目录
    },
}
# ==============================================================


# ================= 持仓历史管理（T+1 限制支持） =================

# 持仓历史文件路径
HOLDINGS_HISTORY_PATH = Path("predictions/holdings_history.json")


def _load_holdings_history():
    """加载持仓历史"""
    if HOLDINGS_HISTORY_PATH.exists():
        with open(HOLDINGS_HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_holdings_history(history):
    """保存持仓历史"""
    HOLDINGS_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HOLDINGS_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def _calculate_hold_days(current_holdings, today_str, hold_thresh=1):
    """
    计算每只股票的持有天数

    参数:
        current_holdings: dict {股票代码: 持仓数量}
        today_str: 当前日期 "YYYY-MM-DD"
        hold_thresh: 最短持有天数

    返回:
        hold_days_dict: dict {股票代码: 持有天数}

    逻辑:
        - 在 history 中有记录 → 计算实际持有天数
        - 在 history 中无记录 → 认为是老持仓，默认可卖（hold_days = hold_thresh + 100）
        - 自动清理 history 中已卖出的股票（不在 current_holdings 中）
    """
    history = _load_holdings_history()
    hold_days_dict = {}
    today = datetime.strptime(today_str, "%Y-%m-%d")

    # 清理已卖出的股票（不在当前持仓中）
    removed = []
    for code in list(history.keys()):
        if code not in current_holdings:
            del history[code]
            removed.append(code)

    if removed:
        print(f"   [清理] 已卖出股票: {', '.join(removed)}")
        _save_holdings_history(history)

    # 计算每只股票的持有天数
    new_holdings = []
    old_holdings = []

    for code, amount in current_holdings.items():
        if code in history:
            # 在历史记录中，计算实际持有天数
            buy_date_str = history[code]["buy_date"]
            buy_date = datetime.strptime(buy_date_str, "%Y-%m-%d")
            hold_days = (today - buy_date).days
            new_holdings.append((code, hold_days, buy_date_str))
        else:
            # 不在历史记录中，认为是老持仓，默认可卖
            hold_days = hold_thresh + 100  # 例如 101 天
            old_holdings.append(code)

        hold_days_dict[code] = hold_days

    # 简洁地输出汇总信息
    if new_holdings:
        print(f"   📊 新买入持仓 ({len(new_holdings)} 只):")
        for code, days, buy_date in new_holdings[:5]:  # 只显示前5个
            print(f"      • {code}: 持有 {days} 天 (买入: {buy_date})")
        if len(new_holdings) > 5:
            print(f"      ... 还有 {len(new_holdings) - 5} 只")

    if old_holdings:
        print(f"   📦 老持仓 ({len(old_holdings)} 只): {', '.join(old_holdings[:8])}")
        if len(old_holdings) > 8:
            print(f"      ... 还有 {len(old_holdings) - 8} 只")

    return hold_days_dict


def _update_holdings_history_after_buy(buy_orders, today_str):
    """
    在生成买入订单后，更新持仓历史

    参数:
        buy_orders: DataFrame 买入订单列表（包含 'stock' 和 'shares' 列）
        today_str: 当前日期 "YYYY-MM-DD"
    """
    history = _load_holdings_history()

    for _, order in buy_orders.iterrows():
        code = order["stock"]
        amount = order["shares"]

        if amount > 0:
            # 只在首次买入时记录日期，避免重复买入重置持有天数
            if code not in history:
                history[code] = {
                    "buy_date": today_str,
                    "amount": int(amount),
                }
                print(f"   [记录] {code} 买入 {amount} 股，日期: {today_str}")
            else:
                # 已有记录，只更新数量
                history[code]["amount"] = int(amount)

    _save_holdings_history(history)


def _cleanup_holdings_history(current_holdings):
    """
    清理已卖出的股票记录

    注意：只在确认持仓变化后调用（例如下一次运行时）
    """
    history = _load_holdings_history()
    removed = []

    for code in list(history.keys()):
        if code not in current_holdings:
            del history[code]
            removed.append(code)

    if removed:
        print(f"   [清理] 已卖出股票: {', '.join(removed)}")
        _save_holdings_history(history)


# ==============================================================


@dataclass
class DataUpdateConfig:
    """描述数据自动更新所需的各项参数。"""

    enable_auto_update: bool = True  # 是否启用自动更新逻辑
    data_source_url: str = "https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz"  # 默认数据源
    download_timeout: int = 600  # 下载超时时间
    temp_dir: Optional[str] = None  # 临时目录位置

    def get_temp_dir(self) -> Path:
        """返回用于下载/解压的临时目录。"""
        if self.temp_dir:
            return Path(self.temp_dir).expanduser()
        return Path.home() / ".qlib" / "temp"


def _provider_path_from_uri(uri: str) -> Path:
    if not uri:
        uri = "~/.qlib/qlib_data/cn_data"
    path = Path(uri)
    if uri.startswith("~"):
        path = path.expanduser()
    return path


def _missing_required_data(data_path: Path, instruments) -> list:
    missing = []
    for dirname in ("features", "instruments", "calendars"):
        if not (data_path / dirname).exists():
            missing.append(str(data_path / dirname))
    if isinstance(instruments, str):
        inst_file = data_path / "instruments" / f"{instruments}.txt"
        if not inst_file.exists():
            missing.append(str(inst_file))
    return missing


def _latest_calendar_date(data_path: Path) -> Optional[str]:
    cal_file = data_path / "calendars" / "day.txt"
    if not cal_file.exists():
        return None
    with cal_file.open("r") as f:
        dates = [line.strip() for line in f if line.strip()]
    return dates[-1] if dates else None


def _is_data_outdated(data_path: Path, target_date: str, instruments) -> tuple:
    latest_local = _latest_calendar_date(data_path)
    if latest_local is None:
        return True, None
    target_ts = pd.Timestamp(target_date)
    required_ts = (target_ts - BDay(1)).normalize()
    if latest_local < required_ts.strftime("%Y-%m-%d"):
        return True, latest_local
    if _missing_required_data(data_path, instruments):
        return True, latest_local
    return False, latest_local


def _download_and_update_data(cfg: DataUpdateConfig, target_path: Path) -> bool:
    try:
        temp_dir = cfg.get_temp_dir()
        temp_dir.mkdir(parents=True, exist_ok=True)
        download_path = temp_dir / "qlib_bin.tar.gz"
        print("[数据] 开始下载最新数据包...")
        print(f"   来源: {cfg.data_source_url}")
        urlretrieve(cfg.data_source_url, download_path)
        print(f"[成功] 下载完成，文件大小 {download_path.stat().st_size / (1024 * 1024):.1f} MB")
        extract_dir = temp_dir / "extracted"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(download_path, "r:gz") as tar:
            tar.extractall(extract_dir)
        qlib_bin_dir = extract_dir / "qlib_bin"
        if not qlib_bin_dir.exists():
            print("[错误] 解压后的目录缺少 qlib_bin 目录")
            return False
        for dirname in ("features", "instruments", "calendars"):
            if not (qlib_bin_dir / dirname).exists():
                print(f"[错误] 缺少必要目录: {dirname}")
                return False
        if target_path.exists():
            shutil.rmtree(target_path)
        shutil.copytree(qlib_bin_dir, target_path)
        print("[成功] 数据更新完成")
        return True
    except Exception as err:
        print(f"[错误] 数据更新失败: {err}")
        return False


def _ensure_data_ready(provider_uri: str, instruments, target_date: str, cfg: DataUpdateConfig):
    data_path = _provider_path_from_uri(provider_uri)
    missing = _missing_required_data(data_path, instruments)
    outdated, latest = _is_data_outdated(data_path, target_date, instruments)
    if not cfg.enable_auto_update:
        if missing:
            print("[警告] 检测到数据缺失:", missing)
        if outdated:
            print(f"[警告] 本地数据最新日期 {latest} 早于 {target_date}")
        return
    if not missing and not outdated:
        print(f"[数据] 本地数据已是最新（最新交易日 {latest}）")
        return
    print("[数据] 触发自动更新...")
    if not _download_and_update_data(cfg, data_path):
        raise RuntimeError("自动更新数据失败，请检查网络或手动更新数据。")


def _resolve_trading_date(target_ts: pd.Timestamp) -> str:
    """
    获取距目标时间最近（含当天）的交易日。
    """
    if target_ts.tzinfo is not None:
        target_ts = target_ts.tz_convert(None)
    target_ts = target_ts.normalize()
    date_str = target_ts.strftime("%Y-%m-%d")
    try:
        pred = get_pre_trading_date(date_str) or date_str
        return pd.Timestamp(pred).strftime("%Y-%m-%d")
    except ValueError:
        window = 60
        while window <= 3650:
            start = (target_ts - pd.Timedelta(days=window)).strftime("%Y-%m-%d")
            cal = D.calendar(start_time=start, end_time=date_str, future=False, freq="day")
            cal = [pd.Timestamp(x) for x in cal if pd.Timestamp(x) <= target_ts]
            if cal:
                return cal[-1].strftime("%Y-%m-%d")
            window *= 2
        raise ValueError(f"无法在 {date_str} 之前找到交易日，请检查数据目录是否完整")


def _read_positions(path: Optional[str]) -> Dict[str, float]:
    """读取 positions_live.csv -> {code: position}。必须包含 code, position/pos 列。"""
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"positions file not found: {p}")
    df = pd.read_csv(p, encoding="utf-8-sig").rename(columns=str.lower)
    code_col = "code"
    pos_col = "position" if "position" in df.columns else "pos"
    if code_col not in df.columns or pos_col not in df.columns:
        raise ValueError(f"positions file must contain columns: code, position/pos; got {df.columns}")
    df = df[[code_col, pos_col]].dropna()

    def _normalize_pos_code(code: str) -> str:
        if code is None:
            return ""
        s = str(code).strip().upper()
        if not s:
            return ""
        if s.endswith(".0"):
            s = s[:-2]
        if "." in s:
            left, right = s.split(".", 1)
            if left.isdigit() and (not right or right.isdigit()):
                s = left
        if s.isdigit() and 0 < len(s) < 6:
            s = s.zfill(6)
        if s.endswith(".SH") or s.endswith(".SZ"):
            return s
        if s.startswith("SH") or s.startswith("SZ"):
            return s
        if len(s) == 6 and s.isdigit():
            if s[0] in {"6", "5", "9"}:
                return f"SH{s}"
            if s[0] in {"0", "3"}:
                return f"SZ{s}"
        return s

    holdings = {}
    cash = None

    for _, row in df.iterrows():
        code_raw = str(row[code_col]).strip().upper()

        # 检查是否是 CASH 行
        if code_raw == "CASH":
            cash = float(row[pos_col])
            print(f"[live] ✅ 从 positions_live.csv 读取账户现金: {cash:.2f} 元")
            continue

        code = _normalize_pos_code(row[code_col])
        if not code:
            continue
        holdings[code] = float(row[pos_col])

    print(f"[live] 读取持仓，共 {len(holdings)} 只，示例: {list(holdings)[:5]}")
    return holdings, cash


def _read_quotes(path: Optional[str]) -> Dict[str, Dict[str, float]]:
    """读取 quotes_live.csv -> {code: {last,bid1,ask1,high_limit,low_limit}}。"""
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"quotes file not found: {p}")
    df = pd.read_csv(p, encoding="utf-8-sig").rename(columns=str.lower)
    required = ["code", "last"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"quotes file missing column: {col}")
    def _normalize_quote_code(code: str) -> str:
        if code is None:
            return ""
        s = str(code).strip().upper()
        if not s:
            return ""
        if "." in s:
            left, right = s.split(".", 1)
            if len(left) == 6 and left.isdigit() and right in {"SH", "SZ"}:
                return f"{right}{left}"
        if s.startswith("SH") or s.startswith("SZ"):
            return s
        if len(s) == 6 and s.isdigit():
            return s
        return s

    records = {}
    for _, r in df.iterrows():
        code = _normalize_quote_code(r["code"])
        if not code:
            continue
        records[code] = {
            "last": float(r.get("last", float("nan"))),
            "bid1": float(r.get("bid1", float("nan"))) if "bid1" in df.columns else float("nan"),
            "ask1": float(r.get("ask1", float("nan"))) if "ask1" in df.columns else float("nan"),
            "high_limit": float(r.get("high_limit", float("nan"))) if "high_limit" in df.columns else float("nan"),
            "low_limit": float(r.get("low_limit", float("nan"))) if "low_limit" in df.columns else float("nan"),
        }
    print(f"[live] 读取报价，共 {len(records)} 只，示例: {list(records)[:5]}")
    return records


def _read_state(path: Path) -> Dict[str, object]:
    """读取 state.json，若不存在返回空 dict。"""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(path: Path, phase: str, version: str, extra: Optional[Dict[str, object]] = None) -> None:
    """写 state.json，带 phase/version/timestamp，使用临时文件后原子重命名。"""
    data = {"phase": phase, "version": version, "timestamp": pd.Timestamp.utcnow().isoformat()}
    if extra:
        data.update(extra)
    tmp = path.with_suffix(".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _wait_for_phase(path: Path, expect_phase: str, expect_version: str, timeout: int) -> None:
    """阻塞等待 state.json 达到期望的 phase+version，超时抛异常。"""
    start = time.time()
    loop_count = 0
    while True:
        loop_count += 1
        st = _read_state(path)
        current_phase = st.get("phase")
        current_version = st.get("version")

        # 每 10 次循环打印一次等待提示
        if loop_count % 10 == 1:
            print(f"   ⏳ 等待中... ({loop_count}s)")

        if current_phase == expect_phase and current_version == expect_version:
            print(f"   ✅ 完成")
            return

        if time.time() - start > timeout:
            raise TimeoutError(f"等待 {expect_phase} version={expect_version} 超时，当前状态: {st or '无'}")
        time.sleep(2)


class LiveDailyPredictionPipeline(DailyPredictionPipeline):
    """两阶段实盘 Pipeline：Phase1 选股，Phase2 用实时报价生成订单。"""

    def __init__(
        self,
        prediction_cfg: PredictionConfig,
        trading_cfg: TradingConfig,
        output_cfg: OutputConfig,
        quotes_live: Optional[Dict[str, Dict[str, float]]] = None,
        **kwargs,
    ):
        # 调用父类初始化
        super().__init__(prediction_cfg, trading_cfg, output_cfg, **kwargs)
        # 保存实时报价
        self.quotes_live: Dict[str, Dict[str, float]] = quotes_live or {}

    def _get_topk_candidates_for_quotes(self, pred_df: pd.DataFrame) -> pd.DataFrame:
        """
        Phase1 专用：获取需要获取实时报价的候选股票列表

        功能：
        - 只按分数排序取 topk
        - 不做任何筛选（价格、预算、可负担性等）
        - 用于生成 symbols_req.csv

        参数：
            pred_df: 包含 instrument, score, target_weight 等列的预测结果

        返回：
            topk 只股票的 DataFrame（按分数降序）
        """
        top_k = int(self.prediction_cfg.top_k or 0)
        if top_k <= 0 or pred_df.empty:
            return pred_df

        # 极简逻辑：按分数排序取前 topk
        result_df = pred_df.sort_values("score", ascending=False).head(top_k).copy()

        # 重新计算权重（确保权重总和为 1.0）
        result_df["target_weight"] = self._compute_weights(
            result_df["score"],
            self.prediction_cfg.weight_method
        )

        print(f"   └─ 已选出 {len(result_df)} 只候选股票")
        return result_df.reset_index(drop=True)

    def _quote_price(self, code: str, is_buy: bool) -> float:
        """从 quotes_live 中取参考价：买用 ask1→last→高停，卖用 bid1→last→低停。"""
        q = self.quotes_live.get(code)
        if not q:
            return float("nan")
        last = q.get("last", float("nan"))
        ask1 = q.get("ask1", float("nan"))
        bid1 = q.get("bid1", float("nan"))
        hi = q.get("high_limit", float("nan"))
        lo = q.get("low_limit", float("nan"))
        if is_buy:
            price = ask1 if pd.notna(ask1) and ask1 > 0 else last
            if pd.notna(hi) and hi > 0:
                price = min(price, hi) if pd.notna(price) and price > 0 else hi
        else:
            price = bid1 if pd.notna(bid1) and bid1 > 0 else last
            if pd.notna(lo) and lo > 0:
                price = max(price, lo) if pd.notna(price) and price > 0 else lo
        return float(price) if pd.notna(price) and price > 0 else float("nan")

    def _fetch_prices(self, instruments):
        """价格获取：优先用 quotes_live，否则回退父类历史价。"""
        prices = []
        missing = []
        for code in instruments:
            price = self._quote_price(code, is_buy=True)
            if pd.notna(price) and price > 0:
                prices.append({"instrument": code, "price": price})
            else:
                missing.append(code)
        if prices and not missing:
            return pd.DataFrame(prices)
        if missing:
            fallback = super()._fetch_prices(missing)
            extra = fallback.to_dict(orient="records") if fallback is not None else []
            prices.extend(extra)
            return pd.DataFrame(prices) if prices else fallback
        return super()._fetch_prices(instruments)

    def _resolve_price(self, exchange, order, trade_start, trade_end, base_df):
        """下单定价：优先 quotes_live，缺失则回退父类逻辑。"""
        is_buy = order.direction == OrderDir.BUY
        code = order.stock_id
        price = self._quote_price(code, is_buy=is_buy)
        if pd.notna(price) and price > 0:
            return float(price)
        return super()._resolve_price(exchange, order, trade_start, trade_end, base_df)

    def _create_trade_calendar_for_single_day(self, trade_date: str):
        """
        为单日实盘创建 TradeCalendarManager

        在实盘场景中，每天只需要生成一次订单，因此创建一个单日的交易日历。

        参数：
        :param trade_date: 交易日期字符串，格式：'YYYY-MM-DD'
        :return: TradeCalendarManager 对象
        """
        from qlib.backtest.utils import TradeCalendarManager

        return TradeCalendarManager(
            freq="day",                           # 交易频率：日频
            start_time=pd.Timestamp(trade_date),  # 交易开始时间
            end_time=pd.Timestamp(trade_date),    # 交易结束时间（同一天=单日）
        )

    def _create_signal_from_predictions(self, pred_df: pd.DataFrame):
        """
        将预测 DataFrame 转换为 Signal 对象

        TopkDropoutStrategy 需要 Signal 对象作为输入。Signal 期望的数据格式是
        MultiIndex Series：(instrument, datetime) -> score

        参数：
        :param pred_df: 预测 DataFrame，必须包含 'instrument' 和 'score' 列
        :return: Signal 对象
        """
        from qlib.backtest.signal import create_signal_from

        # Signal 期望的格式是 MultiIndex: (instrument, datetime)
        # 示例：
        # instrument  datetime
        # SH600000    2025-11-19    0.85
        # SZ000001    2025-11-19    0.72
        # dtype: float64

        # 获取预测日期（T 日），信号使用 T-1 日的数据
        pred_date = pd.Timestamp(self.prediction_cfg.prediction_date)
        # 获取 T-1 日期（用于 signal 的 datetime 索引）
        signal_date = pred_date - pd.Timedelta(days=1)

        # 创建 MultiIndex Series
        df_for_signal = pred_df[['instrument', 'score']].copy()
        df_for_signal['datetime'] = signal_date
        df_for_signal = df_for_signal.set_index(['instrument', 'datetime'])
        signal_series = df_for_signal['score']

        # 创建 Signal 对象（qlib 内部会将 Series 包装为 SignalWCache）
        signal = create_signal_from(signal_series)

        return signal

    def _generate_trading_orders(self, pred_df: pd.DataFrame):
        """覆写：使用 LiveExchange 将 quotes_live 注入 Exchange，再生成订单。"""
        # ========== 第一步：检查交易配置，决定是否生成订单 ==========

        # 检查 enable_trading 标志：如果为 False，则完全跳过交易生成
        if not self.trading_cfg.enable_trading:
            print("\n[提示] 交易生成被关闭，直接返回空结果")
            # 计算价格匹配率和可交易股票数（用于统计）
            price_rate, tradable = self._price_stats(pred_df)
            from daily_predict import TradingResult  # 延迟导入避免循环依赖

            # 返回空的 TradingResult，标记未使用 Exchange
            return TradingResult.empty(price_success_rate=price_rate, tradable_stocks=tradable, exchange_used=False)

        # 检查 use_exchange_system 标志：如果为 False，则不使用 Exchange 系统
        if not self.trading_cfg.use_exchange_system:
            print("\n[提示] Exchange 系统关闭，直接返回空结果")
            # 同样计算统计信息后返回空结果
            price_rate, tradable = self._price_stats(pred_df)
            from daily_predict import TradingResult

            return TradingResult.empty(price_success_rate=price_rate, tradable_stocks=tradable, exchange_used=False)

        # ========== 第二步：验证预测数据有效性 ==========

        print("\n[info] 使用 LiveExchange + 实时报价生成订单...")
        # 检查预测 DataFrame 是否为空
        if pred_df.empty:
            print("   [警告] 预测为空，不产生订单")
            from daily_predict import TradingResult

            # 返回空结果，但标记使用了 Exchange（因为配置允许）
            return TradingResult.empty(exchange_used=True)

        # ========== 第三步：过滤可交易标的 ==========

        # 计算价格匹配率和总可交易股票数
        price_rate, tradable_total = self._price_stats(pred_df)
        # 从预测结果中筛选出可交易的股票（排除涨跌停、停牌等）
        # is_tradable 字段由前面的 _calculate_trading_weights() 设置
        tradable_df = pred_df[pred_df.get("is_tradable", False)].copy()
        # 如果过滤后没有可交易标的，返回空结果
        if tradable_df.empty:
            print("   [警告] 没有可交易标的（涨跌停/停牌）")
            from daily_predict import TradingResult

            return TradingResult.empty(price_success_rate=price_rate, tradable_stocks=0, exchange_used=True)

        # ========== 第四步：初始化 LiveExchange（注入实时行情） ==========

        # 提取可交易股票代码列表，用于 Exchange，选择出来的topk
        stock_pool = tradable_df["instrument"].tolist()
        # 交易开始时间：预测日期当天
        trade_start = pd.Timestamp(self.prediction_cfg.prediction_date)
        # 交易结束时间：预测日期 + 1 天
        trade_end = trade_start + pd.Timedelta(days=1)
        # 起始日期：需要回溯 price_search_days 天以获取历史价格数据
        start_date = (trade_start - pd.Timedelta(days=max(self.trading_cfg.price_search_days, 1))).strftime("%Y-%m-%d")

        # 创建 LiveExchange 实例，将实时行情 quotes_live 注入
        # LiveExchange 会优先使用 quotes_live 中的实时价格（bid1/ask1/last）
        exchange = LiveExchange(
            quotes_live=self.quotes_live,  # 从 iQuant 读取的实时行情
            codes=stock_pool,              # 可交易股票池
            start_time=start_date,         # 数据起始时间
            end_time=trade_end.strftime("%Y-%m-%d"),  # 数据结束时间
            deal_price=self.trading_cfg.deal_price,   # 成交价类型（bid1/ask1/last 等）
            freq=self.trading_cfg.trade_freq,         # 交易频率（day/1min 等）
            # 交易成本参数（符合实际 A 股成本）
            open_cost=self.trading_cfg.open_cost,     # 买入成本率
            close_cost=self.trading_cfg.close_cost,   # 卖出成本率
            min_cost=self.trading_cfg.min_cost,       # 最小成本
            impact_cost=self.trading_cfg.impact_cost, # 滑点成本
        )

        # ========== 第五步：初始化当前持仓 Position（包含持有天数） ==========

        from daily_predict import TradingResult  # 延迟导入避免循环
        from qlib.backtest.position import Position  # 重用 qlib 的 Position 类
        from qlib.contrib.strategy.order_generator import OrderGenWOInteract  # 订单生成器

        # 5.1 计算每只股票的持有天数（支持 T+1 限制）
        print(f"\n[处理] 计算持仓持有天数...")
        hold_days_dict = _calculate_hold_days(
            current_holdings=self.trading_cfg.current_holdings,
            today_str=self.prediction_cfg.prediction_date,
            hold_thresh=self.trading_cfg.hold_thresh,
        )

        # 5.2 初始化 Position，包含 count_day 字段
        position_dict = {}
        for code, amount in self.trading_cfg.current_holdings.items():
            hold_days = hold_days_dict.get(code, self.trading_cfg.hold_thresh + 100)  # 默认可卖
            position_dict[code] = {
                "amount": amount,
                "count_day": hold_days,  # 设置持有天数，确保 T+1 检查生效
            }

        # 5.3 创建 Position 对象
        position = Position(
            cash=self.trading_cfg.total_cash,  # 可用现金（从 iQuant 读取或配置默认值）
            position_dict=position_dict,
        )

        # 如果有持仓，需要填充持仓股票的当前价格，TODO: 这里我觉得应该先从实时报价中获取
        if self.trading_cfg.current_holdings:
            try:
                # 尝试从本地历史数据填充持仓价格
                position.fill_stock_value(start_time=self.prediction_cfg.prediction_date, freq=self.trading_cfg.trade_freq)
            except Exception as err:
                # 如果本地数据缺失（常见于刚更新的数据），提示将使用实时报价
                print(f"   [提示] 本地历史数据缺失({err})，将使用实时报价补齐持仓价格")
            # 使用实时报价补齐持仓价格（从 quotes_live 或 tradable_df 中获取）
            self._ensure_position_prices(position, tradable_df)

        # ========== 第六步：重构 target_weight_position（用于后续计算 target_shares） ==========

        # TopkDropoutStrategy 不需要 target_weight_position，但 _calculate_target_shares() 需要
        # 从 tradable_df 中提取 target_weight 构建字典：{股票代码: 目标权重}
        target_weight_position = dict(zip(tradable_df["instrument"], tradable_df["target_weight"]))
        print(f"   [DEBUG] 重构 target_weight_position: {len(target_weight_position)} 只股票")

        # ========== 第七步：使用 TopkDropoutStrategy 生成订单列表 ==========

        # 从 qlib 导入 TopkDropoutStrategy（内置的 Topk + Dropout 选股策略）
        from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy

        # 7.1 创建单日交易日历（TopkDropoutStrategy 需要）
        trade_calendar = self._create_trade_calendar_for_single_day(
            trade_date=self.prediction_cfg.prediction_date
        )

        # 7.2 将预测 DataFrame 转换为 Signal 对象（TopkDropoutStrategy 需要）
        # Signal 对象内部使用 pd.Series 存储预测分数（index=股票代码，values=分数）
        signal = self._create_signal_from_predictions(pred_df)

        # 7.3 根据配置选择策略类
        # 从配置中读取 use_live_topk_strategy 标志（默认 False）
        # 注意：这个配置来自 DEFAULT_CONFIG 或外部 JSON，通过 trading_cfg 访问可能没有这个字段
        # 因此我们需要从原始配置中读取（如果 Pipeline 保存了的话）
        # 为简化实现，我们直接从 DEFAULT_CONFIG 或环境读取

        # 临时方案：检查环境变量或使用父类的 trading_cfg
        # 更好的方案是在 __init__ 中保存完整配置字典
        use_live_topk = getattr(self.trading_cfg, 'use_live_topk_strategy', False)
        min_afford_shares = getattr(self.trading_cfg, 'min_affordable_shares', 100)

        # 如果启用 LiveTopkStrategy，导入并使用
        if use_live_topk:
            from qlib.contrib.strategy.live_strategy import LiveTopkStrategy
            print("[live] 使用 LiveTopkStrategy（两轮预算分配优化）")
            strategy = LiveTopkStrategy(
                signal=signal,                                 # 预测信号（包含所有股票的分数）
                topk=self.prediction_cfg.top_k,                # 目标持仓数量（例如 20 只）
                n_drop=self.trading_cfg.n_drop,                # Dropout 数量：每次替换几只股票（例如 3 只）
                method_sell="bottom",                          # 卖出方法：卖出分数最低的 n_drop 只
                method_buy="top",                              # 买入方法：买入分数最高的 n_drop 只
                hold_thresh=self.trading_cfg.hold_thresh,      # 最短持有天数（例如 1 天）
                only_tradable=True,                            # 只考虑可交易标的（自动过滤涨跌停、停牌）
                risk_degree=self.trading_cfg.risk_degree,      # 风险度（资金使用比例，0-1）
                # LiveTopkStrategy 专属参数
                min_affordable_shares=min_afford_shares,       # 最小可负担股数（默认 100 股）
                enable_affordability_filter=True,              # 启用两轮分配逻辑
            )
        else:
            # 使用标准的 TopkDropoutStrategy
            from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy
            print("[live] 使用 TopkDropoutStrategy（标准策略）")
            strategy = TopkDropoutStrategy(
                signal=signal,                                 # 预测信号（包含所有股票的分数）
                topk=self.prediction_cfg.top_k,                # 目标持仓数量（例如 20 只）
                n_drop=self.trading_cfg.n_drop,                # Dropout 数量：每次替换几只股票（例如 3 只）
                method_sell="bottom",                          # 卖出方法：卖出分数最低的 n_drop 只
                method_buy="top",                              # 买入方法：买入分数最高的 n_drop 只
                hold_thresh=self.trading_cfg.hold_thresh,      # 最短持有天数（例如 1 天）
                only_tradable=True,                            # 只考虑可交易标的（自动过滤涨跌停、停牌）
                risk_degree=self.trading_cfg.risk_degree,      # 风险度（资金使用比例，0-1）
            )

        # 7.4 设置策略内部状态（通过 Infrastructure 对象）
        # TopkDropoutStrategy 的属性是只读的，需要通过 reset() 方法设置
        from qlib.backtest.utils import LevelInfrastructure, CommonInfrastructure
        from qlib.backtest.account import Account

        # 7.4.1 创建 Account 对象（包装 Position，提供 current_position 属性）
        # Account 需要从 Position 中提取现金和持仓信息
        position_dict = {}
        for stock_id, stock_info in position.position.items():
            if stock_id == "cash" or stock_id == "now_account_value":
                continue
            if isinstance(stock_info, dict):
                position_dict[stock_id] = stock_info
            else:
                position_dict[stock_id] = {"amount": float(stock_info)}

        trade_account = Account(
            init_cash=position.position.get("cash", self.trading_cfg.total_cash),
            position_dict=position_dict,
            freq="day",
        )
        # 用我们已构建的 Position 替换 Account 内部的 position
        trade_account.current_position = position

        # 7.4.2 创建 LevelInfrastructure（包含 trade_calendar）
        level_infra = LevelInfrastructure()
        level_infra.reset_infra(trade_calendar=trade_calendar)

        # 7.4.3 创建 CommonInfrastructure（包含 trade_account 和 trade_exchange）
        common_infra = CommonInfrastructure()
        common_infra.reset_infra(trade_account=trade_account, trade_exchange=exchange)

        # 7.4.4 通过 reset() 方法设置策略的基础设施
        strategy.reset(level_infra=level_infra, common_infra=common_infra)

        # 7.5 调用策略生成订单
        # TopkDropoutStrategy.generate_trade_decision() 返回 TradeDecisionWO 对象
        # 该对象包含订单列表（order_list）
        print(f"   [TopkDropoutStrategy] topk={self.prediction_cfg.top_k}, n_drop={self.trading_cfg.n_drop}, hold_thresh={self.trading_cfg.hold_thresh}")
        trade_decision = strategy.generate_trade_decision()
        orders = trade_decision.order_list  # 提取订单列表（List[Order]）

        # ========== 第八步：处理订单为空的情况 ==========

        # 如果生成的订单列表为空（可能当前持仓已达目标权重）
        if not orders:
            print("   [提示] 生成订单为空")
            return TradingResult.empty(price_success_rate=price_rate, tradable_stocks=len(tradable_df), exchange_used=True)

        # ========== 第九步：转换订单格式并计算金额 ==========

        # 将 Order 对象列表转换为 DataFrame，同时计算买入/卖出总金额
        orders_df, total_buy_amount, total_sell_amount = self._orders_to_frame(
            orders=orders,         # 订单对象列表
            exchange=exchange,     # 交易所对象
            base_df=tradable_df,   # 基础预测数据（用于补充信息）
            trade_start=trade_start,  # 交易开始时间
            trade_end=trade_end,   # 交易结束时间
        )

        # ========== 第九点五步：记录买入订单到持仓历史 ==========

        buy_orders = orders_df[orders_df["action"] == "买入"]
        if len(buy_orders) > 0:
            print(f"\n[记录] 更新持仓历史...")
            _update_holdings_history_after_buy(
                buy_orders=buy_orders,
                today_str=self.prediction_cfg.prediction_date,
            )

        # ========== 第十步：资金预算控制 ==========

        # 计算可用预算：现金 * 风险度 + 卖出回笼的资金
        budget = self.trading_cfg.total_cash * self.trading_cfg.risk_degree + total_sell_amount
        # 如果买入金额超过预算，需要削减买入订单
        if total_buy_amount > budget and budget > 0:
            # 按预算比例缩减买入订单的份额
            orders_df, total_buy_amount = self._cap_buy_orders_to_budget(orders_df, budget)

        # ========== 第十一步：计算目标份额和净投入金额 ==========

        # 计算每只股票的目标持仓份额（用于记录和验证）
        target_shares = self._calculate_target_shares(exchange, target_weight_position, trade_start, trade_end)
        # 计算净投入金额：买入金额 - 卖出金额（取非负）
        net_amount = max(total_buy_amount - total_sell_amount, 0.0)

        # ========== 第十二步：打印详细的持仓和交易信息 ==========

        print(f"\n{'='*60}")
        print(f"[交易分析] 持仓与订单详情")
        print(f"{'='*60}")

        # 1. 当前持仓信息（交易前）
        print(f"\n【交易前持仓】")
        current_holdings = self.trading_cfg.current_holdings
        if current_holdings:
            current_holdings_value = 0.0
            holdings_list = []

            # 遍历所有股票计算总价值
            for code, amount in current_holdings.items():
                # 优先使用 iQuant 实时价格，否则回退到 tradable_df 的历史价格
                price = np.nan
                price_source = ""
                if code in self.quotes_live:
                    price = self.quotes_live[code].get('last', np.nan)
                    price_source = "[实时]"
                elif code in tradable_df['instrument'].values:
                    price = tradable_df[tradable_df['instrument'] == code]['price'].iloc[0]
                    price_source = "[历史]"

                if pd.notna(price) and price > 0:
                    value = amount * price
                    current_holdings_value += value
                    holdings_list.append((code, amount, price, value, price_source))
                else:
                    holdings_list.append((code, amount, np.nan, 0.0, ""))

            # 按市值降序排序
            holdings_list.sort(key=lambda x: x[3], reverse=True)

            print(f"   持仓股票数: {len(current_holdings)} 只")
            # 只显示前 5 只
            for code, amount, price, value, price_source in holdings_list[:5]:
                if pd.notna(price) and price > 0:
                    print(f"   - {code}: {amount:.0f}股 × {price:.2f}元{price_source} = {value:,.0f}元")
                else:
                    print(f"   - {code}: {amount:.0f}股 (价格缺失)")

            if len(holdings_list) > 5:
                print(f"   ... 还有 {len(holdings_list) - 5} 只股票")

            print(f"   持仓总价值: {current_holdings_value:,.0f} 元")
        else:
            print(f"   空仓")

        print(f"   可用现金: {self.trading_cfg.total_cash:,.0f} 元")
        total_assets_before = (current_holdings_value if current_holdings else 0.0) + self.trading_cfg.total_cash
        print(f"   总资产: {total_assets_before:,.0f} 元")

        # 2. 卖出订单详情
        sell_orders = orders_df[orders_df["action"] == "卖出"]
        print(f"\n【卖出订单】")
        if len(sell_orders) == 0:
            print(f"   无卖出订单")
            print(f"   原因：当前持仓 {len(current_holdings)} 只 < topk {self.prediction_cfg.top_k} 只，且持仓股票分数较高")
            print(f"   策略：保留所有持仓，买入新股票补足到 topk")
        elif len(sell_orders) > 0:
            print(f"   卖出股票数: {len(sell_orders)} 只")
            for idx, row in sell_orders.iterrows():
                print(f"   - {row['stock']}: {int(row['shares'])}股 × {row['price']:.2f}元 = {row['amount']:,.0f}元")
            print(f"   卖出总金额: {total_sell_amount:,.0f} 元")

            # 交易成本估算
            sell_cost_estimate = max(total_sell_amount * self.trading_cfg.close_cost, self.trading_cfg.min_cost)
            print(f"   预估卖出成本: {sell_cost_estimate:,.2f} 元 (费率 {self.trading_cfg.close_cost:.4%}, 最小 {self.trading_cfg.min_cost:.0f}元)")

        # 3. 买入订单详情
        buy_orders = orders_df[orders_df["action"] == "买入"]
        print(f"\n【买入订单】")
        if len(buy_orders) > 0:
            print(f"   买入股票数: {len(buy_orders)} 只")
            for idx, row in buy_orders.head(10).iterrows():  # 显示前10只
                print(f"   - {row['stock']}: {int(row['shares'])}股 × {row['price']:.2f}元 = {row['amount']:,.0f}元")
            if len(buy_orders) > 10:
                print(f"   ... 还有 {len(buy_orders) - 10} 只股票")
            print(f"   买入总金额: {total_buy_amount:,.0f} 元")
            if self.trading_cfg.total_cash > 0:
                print(f"   资金使用率: {total_buy_amount / self.trading_cfg.total_cash:.1%}")

            # 交易成本估算
            buy_cost_estimate = max(total_buy_amount * self.trading_cfg.open_cost, self.trading_cfg.min_cost)
            print(f"   预估买入成本: {buy_cost_estimate:,.2f} 元 (费率 {self.trading_cfg.open_cost:.4%}, 最小 {self.trading_cfg.min_cost:.0f}元)")
        else:
            print(f"   无买入订单")

        # 4. 交易后预期持仓
        print(f"\n【交易后预期持仓】")
        if target_shares:
            expected_holdings_value = 0.0
            print(f"   预期持仓股票数: {len(target_shares)} 只")
            # 按持仓金额排序显示
            holdings_with_value = []
            for code, shares in target_shares.items():
                # 优先使用 iQuant 实时价格，否则回退到 tradable_df 的历史价格
                price = np.nan
                price_source = ""
                if code in self.quotes_live:
                    price = self.quotes_live[code].get('last', np.nan)
                    price_source = "[实时]"
                elif code in tradable_df['instrument'].values:
                    price = tradable_df[tradable_df['instrument'] == code]['price'].iloc[0]
                    price_source = "[历史]"

                if pd.notna(price) and price > 0:
                    value = shares * price
                    holdings_with_value.append((code, shares, price, value, price_source))
                    expected_holdings_value += value

            # 按价值降序排序
            holdings_with_value.sort(key=lambda x: x[3], reverse=True)

            for code, shares, price, value, price_source in holdings_with_value[:10]:  # 显示前10只
                weight = value / expected_holdings_value if expected_holdings_value > 0 else 0
                print(f"   - {code}: {shares:.0f}股 × {price:.2f}元{price_source} = {value:,.0f}元 ({weight:.1%})")

            if len(holdings_with_value) > 10:
                print(f"   ... 还有 {len(holdings_with_value) - 10} 只股票")

            print(f"   预期持仓总价值: {expected_holdings_value:,.0f} 元")

            # 预期剩余现金（扣除交易成本）
            buy_cost_total = max(total_buy_amount * self.trading_cfg.open_cost, self.trading_cfg.min_cost) if total_buy_amount > 0 else 0
            sell_cost_total = max(total_sell_amount * self.trading_cfg.close_cost, self.trading_cfg.min_cost) if total_sell_amount > 0 else 0
            total_cost = buy_cost_total + sell_cost_total

            expected_cash = self.trading_cfg.total_cash + total_sell_amount - total_buy_amount - total_cost
            print(f"   预期剩余现金: {expected_cash:,.0f} 元")

            # 预期总资产
            expected_total_assets = expected_holdings_value + expected_cash
            print(f"   预期总资产: {expected_total_assets:,.0f} 元")

            # 资产变化详情
            asset_change = expected_total_assets - total_assets_before
            print(f"\n   资产变化分析:")
            print(f"   - 交易前总资产: {total_assets_before:,.0f} 元")
            print(f"   - 交易后总资产: {expected_total_assets:,.0f} 元")
            print(f"   - 总成本: {total_cost:,.2f} 元 (买入 {buy_cost_total:.2f} + 卖出 {sell_cost_total:.2f})")
            print(f"   - 净变化: {asset_change:+,.2f} 元")
        else:
            print(f"   无持仓")

        print(f"\n{'='*60}\n")

        # ========== 第十三步：返回交易结果 ==========

        return TradingResult(
            orders=orders_df,                      # 订单 DataFrame（包含买卖订单）
            total_buy_amount=total_buy_amount,     # 买入总金额
            total_sell_amount=total_sell_amount,   # 卖出总金额
            net_amount=net_amount,                 # 净投入金额
            price_success_rate=price_rate,         # 价格匹配成功率
            tradable_stocks=len(tradable_df),      # 可交易股票数量
            target_shares=target_shares,           # 目标持仓份额字典
            exchange_used=True,                    # 标记使用了 Exchange 系统
        )


def main(argv=None) -> bool:
    """
    命令行入口：实盘交易的主流程（两阶段握手）

    完整流程：
    1. Phase0: 请求持仓 → 等待 iQuant 导出 positions_live.csv
    2. Phase1: 模型推理 + Topk 选股 → 输出 symbols_req.csv
    3. 等待 iQuant 导出 quotes_live.csv（实时行情）
    4. Phase2: 注入实时行情 + 生成订单 → 输出 orders_to_exec.csv
    5. iQuant 读取订单并执行实盘下单
    """
    # ========== 第一步：命令行参数解析和配置加载 ==========

    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(description="Qlib live daily predict (two-phase, live quotes, config-driven)")
    # 添加 --config 参数：可选的 JSON 配置文件路径
    parser.add_argument("--config", type=str, required=False, help="JSON 配置路径；不传则使用代码内 DEFAULT_CONFIG")
    # 解析命令行参数
    args = parser.parse_args(argv)

    # 根据是否提供配置文件，决定使用外部配置还是默认配置
    if args.config:
        # 使用外部 JSON 配置文件
        cfg_path = Path(args.config)
        if not cfg_path.exists():
            raise FileNotFoundError(f"未找到配置文件: {cfg_path}")
        # 读取并解析 JSON 配置
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        print(f"[live] 使用外部配置: {cfg_path}")
    else:
        # 使用文件顶部定义的 DEFAULT_CONFIG
        cfg = DEFAULT_CONFIG
        print("[live] 未提供 --config，使用 DEFAULT_CONFIG（请根据需要修改文件顶部配置）")

    # ========== 第二步：从配置中提取各部分配置项 ==========

    # 提取 qlib 初始化配置
    qlib_init_cfg = cfg.get("qlib_init", {})
    # 提取预测相关配置（模型、数据集等）
    pred_cfg_raw = cfg.get("prediction", {})
    # 提取数据更新配置（自动下载等）
    data_update_raw = cfg.get("data_update", {})

    # 获取当前日期（UTC，归一化为日期）
    today_ts = pd.Timestamp.utcnow().normalize()
    # 从配置中获取预测日期
    pred_cfg_date = pred_cfg_raw.get("prediction_date")
    # 如果配置中未指定或为 "auto"，则使用今天作为预测日期
    if not pred_cfg_date or str(pred_cfg_date).lower() == "auto":
        target_pred_date = today_ts.strftime("%Y-%m-%d")
    else:
        # 使用配置中指定的日期
        target_pred_date = pd.Timestamp(pred_cfg_date).strftime("%Y-%m-%d")

    # ========== 第三步：构建数据更新配置对象 ==========

    # 先获取默认配置
    default_update_cfg = DataUpdateConfig()
    # 根据配置文件覆盖默认值，构建数据更新配置
    data_update_cfg = DataUpdateConfig(
        # 是否启用自动数据更新（下载最新数据）
        enable_auto_update=bool(
            data_update_raw.get("enable_auto_update", default_update_cfg.enable_auto_update)
        ),
        # 数据源 URL（从哪里下载数据）
        data_source_url=data_update_raw.get("data_source_url", default_update_cfg.data_source_url),
        # 下载超时时间（秒）
        download_timeout=int(data_update_raw.get("download_timeout", default_update_cfg.download_timeout)),
        # 临时文件存储目录
        temp_dir=data_update_raw.get("temp_dir", default_update_cfg.temp_dir),
    )

    # ========== 第四步：提取核心配置参数 ==========

    # 数据存储路径（本地 qlib 数据目录）
    # 优先使用 prediction 配置，其次使用 qlib_init 配置
    provider_uri = pred_cfg_raw.get("provider_uri") or qlib_init_cfg.get("provider_uri", "~/.qlib/qlib_data/cn_data")
    # 市场区域（cn=中国，us=美国等）
    region = pred_cfg_raw.get("region") or qlib_init_cfg.get("region", "cn")
    # 股票池（csi300=沪深300，csi500=中证500等）
    instruments = pred_cfg_raw.get("instruments", "csi300")

    # 打印关键信息
    print(f"[live] 目标预测日: {target_pred_date}")
    print(f"[live] 检查本地数据: provider={provider_uri}")
    # 确保本地数据准备就绪（如有需要会自动下载）
    _ensure_data_ready(provider_uri, instruments, target_pred_date, data_update_cfg)

    # ========== 第五步：初始化 qlib 数据引擎 ==========

    import qlib
    # 并行计算核心数（用于特征计算加速）
    kernels = qlib_init_cfg.get("kernels", 1)
    # joblib 后端类型（threading=线程，multiprocessing=进程）
    joblib_backend = qlib_init_cfg.get("joblib_backend", "threading")
    # 每个子进程最大任务数（用于内存管理）
    maxtasksperchild = qlib_init_cfg.get("maxtasksperchild", 1)

    print(f"[live] 初始化 qlib: provider_uri={provider_uri}, region={region}")
    # 调用 qlib.init() 初始化数据引擎
    qlib.init(
        provider_uri=provider_uri,      # 数据路径
        region=region,                  # 市场区域
        kernels=kernels,                # 并行核心数
        joblib_backend=joblib_backend,  # 并行后端
        maxtasksperchild=maxtasksperchild,  # 子进程任务数限制
    )
    print("[live] qlib 初始化完成")

    # ========== 第六步：读取文件路径和运行时配置 ==========

    # 提取路径配置
    paths = cfg.get("paths", {})
    # positions_live.csv 的路径（iQuant 导出的持仓文件）
    positions_path = paths.get("positions")
    # quotes_live.csv 的路径（iQuant 导出的实时行情）
    quotes_path = paths.get("quotes")
    # symbols_req.csv 的输出路径（qlib 输出给 iQuant 的选股请求）
    symbols_out = paths.get("symbols_out", "predictions/symbols_req.csv")
    # orders_to_exec.csv 的输出路径（qlib 输出给 iQuant 的订单文件）
    orders_out = paths.get("orders_out", "predictions/orders_to_exec.csv")
    # state.json 的路径（握手状态文件，用于阶段同步）
    state_path = Path(paths.get("state", "predictions/state.json"))

    # 提取运行时配置
    runtime = cfg.get("runtime", {})
    # 等待超时时间（秒），用于等待 iQuant 响应
    wait_secs = int(runtime.get("wait_secs", 300))
    # 版本号（用于握手时识别批次，默认使用当前时间戳）
    version = runtime.get("version") or pd.Timestamp.utcnow().strftime("%Y%m%d%H%M%S")

    # ========== 第七步：启动时重置 state.json（清理旧状态） ==========

    # 如果存在旧的 state.json，删除它以避免状态冲突
    if state_path.exists():
        print(f"[live] 检测到旧的 state.json，正在重置...")
        try:
            state_path.unlink()  # 删除旧文件
            print(f"[live] 已删除旧的 state.json")
        except Exception as e:
            print(f"[live] [WARN] 无法删除旧的 state.json: {e}")

    # ========== 第八步：Phase0 - 请求持仓并等待 iQuant 响应 ==========

    # 写入 state.json: phase=positions_needed（告知 iQuant 需要持仓数据）
    print(f"\n📊 [Phase 0] 获取账户数据")
    print(f"   ├─ 请求持仓数据...")
    _write_state(state_path, phase="positions_needed", version=version, extra={})

    # 等待 iQuant 写入 state.json: phase=positions_ready（表示持仓已导出）
    print(f"   ├─ 等待 iQuant 导出...")
    _wait_for_phase(state_path, expect_phase="positions_ready", expect_version=version, timeout=wait_secs)

    # 读取 iQuant 导出的 positions_live.csv
    # 返回 (holdings, cash)：持仓字典 + 账户现金
    holdings, cash_from_iquant = _read_positions(positions_path)

    # 检查是否成功读取账户现金（CASH 行）
    if cash_from_iquant is not None:
        print(f"   ├─ 持仓: {len(holdings)} 只股票")
        print(f"   └─ 现金: {cash_from_iquant:,.2f} 元 ✅")
    else:
        print(f"   ├─ 持仓: {len(holdings)} 只股票")
        print(f"   └─ 现金: 未读取 ⚠️")

    # ========== 第九步：构建预测配置对象 PredictionConfig ==========

    base_pred_cfg = PredictionConfig(
        # MLflow 实验 ID（用于定位模型）
        experiment_id=pred_cfg_raw.get("experiment_id", "866149675032491302"),
        # MLflow recorder ID（用于加载特定模型版本）
        recorder_id=pred_cfg_raw.get("recorder_id", "3d0e78192f384bb881c63b32f743d5f8"),
        # 预测日期（T 日）
        prediction_date=target_pred_date,
        # Topk 选股数量（从预测结果中选择得分最高的 top_k 只）
        top_k=pred_cfg_raw.get("top_k", 20),
        # 最小得分阈值（低于此分数的股票不参与选股）
        min_score_threshold=pred_cfg_raw.get("min_score_threshold", 0.0),
        # 权重分配方法（equal=等权，score=按分数加权）
        weight_method=pred_cfg_raw.get("weight_method", "equal"),
        # 数据路径
        provider_uri=provider_uri,
        # 市场区域
        region=region,
        # 股票池
        instruments=instruments,
        # 数据集类（DatasetH 是 qlib 的标准数据集类）
        dataset_class=pred_cfg_raw.get("dataset_class", "DatasetH"),
        # 数据集模块路径
        dataset_module=pred_cfg_raw.get("dataset_module", "qlib.data.dataset"),
        # 数据处理器类（Alpha158 是常用的 158 因子）
        handler_class=pred_cfg_raw.get("handler_class", "Alpha158"),
        # 数据处理器模块路径
        handler_module=pred_cfg_raw.get("handler_module", "qlib.contrib.data.handler"),
        # 最小历史数据天数（用于计算特征，如需要 120 天历史数据）
        min_history_days=pred_cfg_raw.get("min_history_days", 120)
    )

    # ========== 第十步：构建交易配置对象 TradingConfig ==========

    # 提取交易相关配置
    trading_raw = cfg.get("trading", {})

    # 决定使用的现金金额：优先使用从 iQuant 读取的实际值，否则使用配置默认值
    config_cash = trading_raw.get("total_cash", 50000)
    if cash_from_iquant is not None:
        # 使用从 iQuant 读取的实际账户现金
        actual_cash = cash_from_iquant
        print(f"[live] ✅ 使用从 iQuant 读取的实际总资金: {actual_cash:.2f} 元")
    else:
        # 回退到配置文件中的默认值
        actual_cash = config_cash
        print(f"[live] ⚠️  使用配置文件的默认总资金: {actual_cash:.2f} 元（建议检查 iQuant 账户现金获取逻辑）")

    # 构建交易配置对象
    base_trading_cfg = TradingConfig(
        # 是否启用交易生成
        enable_trading=trading_raw.get("enable_trading", True),
        # 是否使用 Exchange 系统（用于模拟撮合和价格获取）
        use_exchange_system=trading_raw.get("use_exchange_system", True),
        # 总现金（使用从 iQuant 读取的实际值）
        total_cash=actual_cash,
        # 最小购买份额（A 股最小 100 股）
        min_shares=trading_raw.get("min_shares", 100),
        # 价格搜索天数（回溯多少天寻找有效价格）
        price_search_days=trading_raw.get("price_search_days", 5),
        # 当前持仓（从 iQuant 读取的实际持仓）
        current_holdings=holdings,
        # 资金使用比例（风险度，0-1，控制实际使用的资金占比）
        risk_degree=trading_raw.get("risk_degree", 0.95),
        # TopkDropoutStrategy 相关参数
        # Dropout 换仓数量（每次交易替换的股票数量，默认 3 只）
        n_drop=trading_raw.get("n_drop", 3),
        # 持有天数控制（最短持有天数，默认 1 天，单位：交易日）
        hold_thresh=trading_raw.get("hold_thresh", 1),
        # LiveTopkStrategy 相关参数（小资金优化）
        use_live_topk_strategy=trading_raw.get("use_live_topk_strategy", False),
        min_affordable_shares=trading_raw.get("min_affordable_shares", 100),
        # 交易成本参数（符合实际 A 股成本）
        open_cost=trading_raw.get("open_cost", 0.0003),
        close_cost=trading_raw.get("close_cost", 0.0013),
        min_cost=trading_raw.get("min_cost", 1.0),
        impact_cost=trading_raw.get("impact_cost", 0.0),
    )

    # ========== 第十一步：构建输出配置和 Pipeline 对象 ==========

    # 创建输出配置对象（使用默认配置）
    output_cfg = OutputConfig()

    # 构建实盘预测 Pipeline
    # 注意：Phase1 不需要实时行情，quotes_live 在 Phase2 才注入
    pipeline = LiveDailyPredictionPipeline(
        prediction_cfg=base_pred_cfg,      # 预测配置
        trading_cfg=base_trading_cfg,      # 交易配置
        output_cfg=output_cfg,              # 输出配置
        quotes_live={},                     # Phase1 不需要行情，先传空字典
        data_update_cfg=data_update_cfg,    # 数据更新配置
        enable_detailed_logs=True,          # 启用详细日志
    )

    # ========== 第十二步：Phase1 - 模型推理 + Topk 选股 ==========

    print(f"🤖 [Phase 1] 模型推理与选股")
    print(f"   ├─ 正在初始化环境...")
    # 初始化 qlib 环境（加载配置、检查数据等）
    pipeline._init_environment()
    # 加载 MLflow recorder（用于访问实验记录）
    pipeline.recorder = pipeline._load_recorder()
    # 从 recorder 中加载训练好的模型
    print(f"   ├─ 正在加载模型...")
    pipeline.model = pipeline._load_model(pipeline.recorder)
    # 构建数据集（特征计算、数据处理）
    print(f"   ├─ 正在构建数据集...")
    pipeline.dataset = pipeline._build_dataset()

    # 生成模型预测（对股票池中的所有股票进行打分）
    print(f"   ├─ 正在执行预测...")
    preds = pipeline._generate_predictions()
    # 准备预测结果（转换为 DataFrame，添加 instrument 列等）
    pred_df = pipeline._prepare_predictions(preds)
    # 附加市场数据（价格、涨跌停等信息）
    pred_df = pipeline._attach_market_data(pred_df)
    # Phase1: 选择 Topk 候选股票用于获取实时报价（不做筛选，输出完整的 topk）
    print(f"   ├─ 正在选择候选股票...")
    pred_df = pipeline._get_topk_candidates_for_quotes(pred_df)

    # ========== 第十三步：生成选股请求文件 symbols_req.csv ==========

    # 提取需要的列：股票代码、分数、目标权重
    req_cols = ["instrument", "score", "target_weight"]
    req_df = pred_df[req_cols].copy()

    # 获取当前持仓的股票代码集合
    holding_symbols = set(holdings.keys())
    # 获取选股候选的股票代码集合
    candidate_symbols = set(req_df["instrument"].dropna().tolist())
    # 计算持仓中但不在候选中的股票（需要获取这些股票的实时报价以便决定是否卖出）
    extra_symbols = sorted(holding_symbols - candidate_symbols)

    # 如果有额外的持仓股票，添加到 symbols_req 中
    if extra_symbols:
        # 为这些股票创建占位行（score 和 target_weight 为 NaN）
        extra_df = pd.DataFrame(
            {
                "instrument": extra_symbols,
                "score": [float("nan")] * len(extra_symbols),
                "target_weight": [float("nan")] * len(extra_symbols),
            }
        )
        # 合并到请求 DataFrame
        req_df = pd.concat([req_df, extra_df], ignore_index=True)

    # 写入 symbols_req.csv（告知 iQuant 需要获取哪些股票的实时行情）
    symbols_path = Path(symbols_out)
    symbols_path.parent.mkdir(parents=True, exist_ok=True)  # 确保父目录存在
    symbols_path.write_text(req_df.to_csv(index=False), encoding="utf-8-sig")

    # 写入 state.json: phase=symbols_ready（告知 iQuant 选股完成，可以获取行情了）
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _write_state(state_path, phase="symbols_ready", version=version, extra={"symbols": symbols_path.name})

    print(f"   └─ 已输出选股文件 ({len(req_df)} 只) ✅\n")

    # ========== 第十四步：等待 iQuant 导出实时行情 quotes_live.csv ==========

    # 等待 iQuant 写入 state.json: phase=quotes_ready（表示行情已导出）
    print(f"💱 [Phase 1.5] 获取实时行情")
    print(f"   ├─ 等待 iQuant 导出...")
    _wait_for_phase(state_path, expect_phase="quotes_ready", expect_version=version, timeout=wait_secs)

    # 读取 iQuant 导出的 quotes_live.csv（实时行情：last/bid1/ask1/涨跌停等）
    quotes_live = _read_quotes(quotes_path)
    # 将实时行情注入 Pipeline（Phase2 需要使用）
    pipeline.quotes_live = quotes_live
    print(f"   └─ 已获取 {len(quotes_live)} 只股票行情 ✅\n")

    # ========== 第十五步：Phase2 - 注入实时行情，生成订单 ==========

    print(f"📝 [Phase 2] 生成交易订单")
    # 调用 _generate_trading_orders，使用 LiveExchange + quotes_live 生成订单
    trading_result = pipeline._generate_trading_orders(pred_df)
    # 提取订单 DataFrame（如果生成失败则为空 DataFrame）
    orders_df = trading_result.orders if trading_result else pd.DataFrame()

    # 写入 orders_to_exec.csv（告知 iQuant 需要执行的订单）
    orders_path = Path(orders_out)
    orders_path.parent.mkdir(parents=True, exist_ok=True)  # 确保父目录存在
    orders_path.write_text(orders_df.to_csv(index=False), encoding="utf-8-sig")
    print(f"\n✅ [完成] 已输出订单文件 ({len(orders_df)} 条)\n   路径: {orders_path}")

    # 写入 state.json: phase=orders_ready（告知 iQuant 订单已生成，可以执行下单了）
    _write_state(state_path, phase="orders_ready", version=version, extra={"orders": orders_path.name})

    # ========== 第十六步：流程完成，返回成功 ==========

    # 返回 True 表示整个流程执行成功
    # 后续 iQuant 会读取 orders_to_exec.csv 并执行实盘下单
    return True


if __name__ == "__main__":
    ok = main()
    if ok:
        print("[live] done")
    else:
        print("[live] failed")

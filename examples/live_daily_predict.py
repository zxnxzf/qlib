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
from pathlib import Path  # 路径处理
from typing import Dict, Optional  # 类型注解
from urllib.request import urlretrieve
import sys  # 修改 sys.path 便于导入

import pandas as pd  # 数据处理
from pandas.tseries.offsets import BDay

from qlib.utils import get_pre_trading_date  # 获取前一交易日
from qlib.backtest.decision import OrderDir  # 订单方向枚举
from qlib.backtest.live_exchange import LiveExchange  # 实时报价 Exchange
from qlib.data import D

# 调整 sys.path，保证能导入同级 daily_predict（examples 目录）
_EXAMPLES_DIR = Path(__file__).resolve().parent
if str(_EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES_DIR))

# 从同级 daily_predict 导入原有配置和 Pipeline
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
        "region": "cn",                                 # 市场区域
        "kernels": 1,
        "joblib_backend": "threading",
        "maxtasksperchild": 1
    },
    "paths": {
        "positions": "predictions/positions_live.csv",   # 持仓文件
        "quotes": "predictions/quotes_live.csv",         # 当日行情（ask1/bid1/last/limits）
        "symbols_out": "predictions/symbols_req.csv",    # Phase1 输出
        "orders_out": "predictions/orders_to_exec.csv",  # Phase2 输出
        "state": "predictions/state.json"                # 状态文件
    },
    "runtime": {
        "version": None,    # 流水号；None 则自动用当前时间戳
        "wait_secs": 300    # 等待 quotes_ready 超时时间（秒）
    },
    "prediction": {
        "experiment_id": "866149675032491302",
        "recorder_id": "3d0e78192f384bb881c63b32f743d5f8",
        "prediction_date": "auto",   # auto=用最新交易日(T-1)
        "top_k": 20,
        "min_score_threshold": 0.0,
        "weight_method": "equal",
        "provider_uri": "~/.qlib/qlib_data/cn_data",
        "region": "cn",
        "instruments": "csi300",
        "dataset_class": "DatasetH",
        "dataset_module": "qlib.data.dataset",
        "handler_class": "Alpha158",
        "handler_module": "qlib.contrib.data.handler",
        "min_history_days": 120
    },
    "trading": {
        "enable_trading": True,
        "use_exchange_system": True,
        "total_cash": 50000,
        "max_stock_price": None,
        "dropout_rate": 0.0,
        "min_shares": 100,
        "price_search_days": 5,
        "risk_degree": 0.95
    },
    "data_update": {
        "enable_auto_update": True,
        "data_source_url": "https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz",
        "download_timeout": 600,
        "temp_dir": None,
    },
}
# ==============================================================


@dataclass
class DataUpdateConfig:
    enable_auto_update: bool = True
    data_source_url: str = "https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz"
    download_timeout: int = 600
    temp_dir: Optional[str] = None

    def get_temp_dir(self) -> Path:
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
    for _, row in df.iterrows():
        code = _normalize_pos_code(row[code_col])
        if not code:
            continue
        holdings[code] = float(row[pos_col])
    print(f"[live] 读取持仓，共 {len(holdings)} 只，示例: {list(holdings)[:5]}")
    return holdings


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

        # 每 5 次循环打印一次调试信息
        if loop_count % 5 == 1:
            print(f"[live] 轮询 state.json (第 {loop_count} 次):")
            print(f"   路径: {path}")
            print(f"   期待: phase={expect_phase}, version={expect_version}")
            print(f"   当前: phase={current_phase}, version={current_version}")

        if current_phase == expect_phase and current_version == expect_version:
            print(f"[live] ✓ 检测到期待的状态: phase={expect_phase}, version={expect_version}")
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

    def _generate_trading_orders(self, pred_df: pd.DataFrame):
        """覆写：使用 LiveExchange 将 quotes_live 注入 Exchange，再生成订单。"""
        if not self.trading_cfg.enable_trading:
            print("\n[提示] 交易生成被关闭，直接返回空结果")
            price_rate, tradable = self._price_stats(pred_df)
            from daily_predict import TradingResult  # 延迟导入

            return TradingResult.empty(price_success_rate=price_rate, tradable_stocks=tradable, exchange_used=False)

        if not self.trading_cfg.use_exchange_system:
            print("\n[提示] Exchange 系统关闭，直接返回空结果")
            price_rate, tradable = self._price_stats(pred_df)
            from daily_predict import TradingResult

            return TradingResult.empty(price_success_rate=price_rate, tradable_stocks=tradable, exchange_used=False)

        print("\n[info] 使用 LiveExchange + 实时报价生成订单...")
        if pred_df.empty:
            print("   [警告] 预测为空，不产生订单")
            from daily_predict import TradingResult

            return TradingResult.empty(exchange_used=True)

        price_rate, tradable_total = self._price_stats(pred_df)
        tradable_df = pred_df[pred_df.get("is_tradable", False)].copy()
        if tradable_df.empty:
            print("   [警告] 没有可交易标的（涨跌停/停牌）")
            from daily_predict import TradingResult

            return TradingResult.empty(price_success_rate=price_rate, tradable_stocks=0, exchange_used=True)

        stock_pool = tradable_df["instrument"].tolist()
        trade_start = pd.Timestamp(self.prediction_cfg.prediction_date)
        trade_end = trade_start + pd.Timedelta(days=1)
        start_date = (trade_start - pd.Timedelta(days=max(self.trading_cfg.price_search_days, 1))).strftime("%Y-%m-%d")
        # 用 LiveExchange 注入 quotes_live
        exchange = LiveExchange(
            quotes_live=self.quotes_live,
            codes=stock_pool,
            start_time=start_date,
            end_time=trade_end.strftime("%Y-%m-%d"),
            deal_price=self.trading_cfg.deal_price,
            freq=self.trading_cfg.trade_freq,
        )

        from daily_predict import TradingResult  # 延迟导入避免循环
        from qlib.backtest.position import Position  # 这里重用原 Position
        from qlib.contrib.strategy.order_generator import OrderGenWOInteract

        position = Position(
            cash=self.trading_cfg.total_cash,
            position_dict={code: {"amount": amount} for code, amount in self.trading_cfg.current_holdings.items()},
        )
        if self.trading_cfg.current_holdings:
            try:
                position.fill_stock_value(start_time=self.prediction_cfg.prediction_date, freq=self.trading_cfg.trade_freq)
            except Exception as err:
                print(f"   [提示] 本地历史数据缺失({err})，将使用实时报价补齐持仓价格")
            self._ensure_position_prices(position, tradable_df)

        order_generator = OrderGenWOInteract()
        target_weight_position = dict(zip(tradable_df["instrument"], tradable_df["target_weight"]))
        orders = order_generator.generate_order_list_from_target_weight_position(
            current=position,
            trade_exchange=exchange,
            target_weight_position=target_weight_position,
            risk_degree=self.trading_cfg.risk_degree,
            pred_start_time=trade_start,
            pred_end_time=trade_end,
            trade_start_time=trade_start,
            trade_end_time=trade_end,
        )

        if not orders:
            print("   [提示] 生成订单为空")
            return TradingResult.empty(price_success_rate=price_rate, tradable_stocks=len(tradable_df), exchange_used=True)

        orders_df, total_buy_amount, total_sell_amount = self._orders_to_frame(
            orders=orders,
            exchange=exchange,
            base_df=tradable_df,
            trade_start=trade_start,
            trade_end=trade_end,
        )
        budget = self.trading_cfg.total_cash * self.trading_cfg.risk_degree + total_sell_amount
        if total_buy_amount > budget and budget > 0:
            orders_df, total_buy_amount = self._cap_buy_orders_to_budget(orders_df, budget)
        target_shares = self._calculate_target_shares(exchange, target_weight_position, trade_start, trade_end)
        net_amount = max(total_buy_amount - total_sell_amount, 0.0)

        print(f"   生成买入订单数: {len(orders_df[orders_df['action'] == '买入'])} 条")
        print(f"   预计买入金额: {total_buy_amount:,.0f} 元")
        if self.trading_cfg.total_cash > 0:
            print(f"   资金使用率: {total_buy_amount / self.trading_cfg.total_cash:.1%}")

        return TradingResult(
            orders=orders_df,
            total_buy_amount=total_buy_amount,
            total_sell_amount=total_sell_amount,
            net_amount=net_amount,
            price_success_rate=price_rate,
            tradable_stocks=len(tradable_df),
            target_shares=target_shares,
            exchange_used=True,
        )


def main(argv=None) -> bool:
    """命令行入口：读取持仓/行情，跑两阶段，输出 symbols_req 和 orders_to_exec，并写 state.json。"""
    parser = argparse.ArgumentParser(description="Qlib live daily predict (two-phase, live quotes, config-driven)")
    parser.add_argument("--config", type=str, required=False, help="JSON 配置路径；不传则使用代码内 DEFAULT_CONFIG")
    args = parser.parse_args(argv)
    if args.config:
        cfg_path = Path(args.config)
        if not cfg_path.exists():
            raise FileNotFoundError(f"未找到配置文件: {cfg_path}")
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        print(f"[live] 使用外部配置: {cfg_path}")
    else:
        cfg = DEFAULT_CONFIG
        print("[live] 未提供 --config，使用 DEFAULT_CONFIG（请根据需要修改文件顶部配置）")

    qlib_init_cfg = cfg.get("qlib_init", {})
    pred_cfg_raw = cfg.get("prediction", {})
    data_update_raw = cfg.get("data_update", {})
    today_ts = pd.Timestamp.utcnow().normalize()
    pred_cfg_date = pred_cfg_raw.get("prediction_date")
    if not pred_cfg_date or str(pred_cfg_date).lower() == "auto":
        target_pred_date = today_ts.strftime("%Y-%m-%d")
    else:
        target_pred_date = pd.Timestamp(pred_cfg_date).strftime("%Y-%m-%d")

    default_update_cfg = DataUpdateConfig()
    data_update_cfg = DataUpdateConfig(
        enable_auto_update=bool(
            data_update_raw.get("enable_auto_update", default_update_cfg.enable_auto_update)
        ),
        data_source_url=data_update_raw.get("data_source_url", default_update_cfg.data_source_url),
        download_timeout=int(data_update_raw.get("download_timeout", default_update_cfg.download_timeout)),
        temp_dir=data_update_raw.get("temp_dir", default_update_cfg.temp_dir),
    )

    provider_uri = pred_cfg_raw.get("provider_uri") or qlib_init_cfg.get("provider_uri", "~/.qlib/qlib_data/cn_data")
    region = pred_cfg_raw.get("region") or qlib_init_cfg.get("region", "cn")
    instruments = pred_cfg_raw.get("instruments", "csi300")

    print(f"[live] 目标预测日: {target_pred_date}")
    print(f"[live] 检查本地数据: provider={provider_uri}")
    _ensure_data_ready(provider_uri, instruments, target_pred_date, data_update_cfg)

    # 初始化 qlib（参考 daily_predict.py）
    import qlib
    kernels = qlib_init_cfg.get("kernels", 1)
    joblib_backend = qlib_init_cfg.get("joblib_backend", "threading")
    maxtasksperchild = qlib_init_cfg.get("maxtasksperchild", 1)

    print(f"[live] 初始化 qlib: provider_uri={provider_uri}, region={region}")
    qlib.init(
        provider_uri=provider_uri,
        region=region,
        kernels=kernels,
        joblib_backend=joblib_backend,
        maxtasksperchild=maxtasksperchild,
    )
    print("[live] qlib 初始化完成")

    # 读取路径配置
    paths = cfg.get("paths", {})
    positions_path = paths.get("positions")
    quotes_path = paths.get("quotes")
    symbols_out = paths.get("symbols_out", "predictions/symbols_req.csv")
    orders_out = paths.get("orders_out", "predictions/orders_to_exec.csv")
    state_path = Path(paths.get("state", "predictions/state.json"))

    # 运行参数
    runtime = cfg.get("runtime", {})
    wait_secs = int(runtime.get("wait_secs", 300))
    version = runtime.get("version") or pd.Timestamp.utcnow().strftime("%Y%m%d%H%M%S")

    # ===== 启动时重置 state.json =====
    if state_path.exists():
        print(f"[live] 检测到旧的 state.json，正在重置...")
        try:
            state_path.unlink()  # 删除旧文件
            print(f"[live] 已删除旧的 state.json")
        except Exception as e:
            print(f"[live] [WARN] 无法删除旧的 state.json: {e}")

    # ===== Phase0: 请求持仓，等待 positions_ready =====
    print(f"[live] 请求持仓，写 state=positions_needed, version={version}")
    _write_state(state_path, phase="positions_needed", version=version, extra={})
    print(f"[live] 等待 positions_ready (version={version}) ...")
    _wait_for_phase(state_path, expect_phase="positions_ready", expect_version=version, timeout=wait_secs)
    holdings = _read_positions(positions_path)
    print(f"[live] 读取到持仓: {len(holdings)} 只股票")

    # 预测配置
    base_pred_cfg = PredictionConfig(
        experiment_id=pred_cfg_raw.get("experiment_id", "866149675032491302"),
        recorder_id=pred_cfg_raw.get("recorder_id", "3d0e78192f384bb881c63b32f743d5f8"),
        prediction_date=target_pred_date,
        top_k=pred_cfg_raw.get("top_k", 20),
        min_score_threshold=pred_cfg_raw.get("min_score_threshold", 0.0),
        weight_method=pred_cfg_raw.get("weight_method", "equal"),
        provider_uri=provider_uri,
        region=region,
        instruments=instruments,
        dataset_class=pred_cfg_raw.get("dataset_class", "DatasetH"),
        dataset_module=pred_cfg_raw.get("dataset_module", "qlib.data.dataset"),
        handler_class=pred_cfg_raw.get("handler_class", "Alpha158"),
        handler_module=pred_cfg_raw.get("handler_module", "qlib.contrib.data.handler"),
        min_history_days=pred_cfg_raw.get("min_history_days", 120)
    )

    # 交易配置
    trading_raw = cfg.get("trading", {})
    base_trading_cfg = TradingConfig(
        enable_trading=trading_raw.get("enable_trading", True),
        use_exchange_system=trading_raw.get("use_exchange_system", True),
        total_cash=trading_raw.get("total_cash", 50000),
        max_stock_price=trading_raw.get("max_stock_price", None),
        dropout_rate=trading_raw.get("dropout_rate", 0.0),
        min_shares=trading_raw.get("min_shares", 100),
        price_search_days=trading_raw.get("price_search_days", 5),
        current_holdings=holdings,
    )

    output_cfg = OutputConfig()

    # 构造实盘 Pipeline（Phase1 不需要 quotes_live）
    pipeline = LiveDailyPredictionPipeline(
        prediction_cfg=base_pred_cfg,
        trading_cfg=base_trading_cfg,
        output_cfg=output_cfg,
        quotes_live={},  # Phase1 不需要行情，Phase2 再注入
        data_update_cfg=data_update_cfg,
        enable_detailed_logs=True,
    )

    # Phase1：推理 + Topk 选股
    pipeline._init_environment()
    pipeline.recorder = pipeline._load_recorder()
    pipeline.model = pipeline._load_model(pipeline.recorder)
    pipeline.dataset = pipeline._build_dataset()

    preds = pipeline._generate_predictions()
    pred_df = pipeline._prepare_predictions(preds)
    pred_df = pipeline._attach_market_data(pred_df)
    pred_df = pipeline._select_buyable_topk(pred_df)

    req_cols = ["instrument", "score", "target_weight"]
    req_df = pred_df[req_cols].copy()
    holding_symbols = set(holdings.keys())
    candidate_symbols = set(req_df["instrument"].dropna().tolist())
    extra_symbols = sorted(holding_symbols - candidate_symbols)
    if extra_symbols:
        extra_df = pd.DataFrame(
            {
                "instrument": extra_symbols,
                "score": [float("nan")] * len(extra_symbols),
                "target_weight": [float("nan")] * len(extra_symbols),
            }
        )
        req_df = pd.concat([req_df, extra_df], ignore_index=True)
        print(f"[live] 附加持仓代码 {len(extra_symbols)} 只以便获取实时报价: {extra_symbols[:5]}")

    symbols_path = Path(symbols_out)
    symbols_path.parent.mkdir(parents=True, exist_ok=True)
    symbols_path.write_text(req_df.to_csv(index=False), encoding="utf-8-sig")
    print(f"[live] symbols_req -> {symbols_path}")
    # 写 state：symbols_ready
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _write_state(state_path, phase="symbols_ready", version=version, extra={"symbols": symbols_path.name})

    # 等待 quotes_ready
    print(f"[live] 等待 quotes_ready (version={version}) ...")
    _wait_for_phase(state_path, expect_phase="quotes_ready", expect_version=version, timeout=wait_secs)
    quotes_live = _read_quotes(quotes_path)
    pipeline.quotes_live = quotes_live

    # Phase2：注入 quotes_live，用 LiveExchange 生成订单
    trading_result = pipeline._generate_trading_orders(pred_df)
    orders_df = trading_result.orders if trading_result else pd.DataFrame()
    orders_path = Path(orders_out)
    orders_path.parent.mkdir(parents=True, exist_ok=True)
    orders_path.write_text(orders_df.to_csv(index=False), encoding="utf-8-sig")
    print(f"[live] orders_to_exec -> {orders_path}")
    # 写 state：orders_ready
    _write_state(state_path, phase="orders_ready", version=version, extra={"orders": orders_path.name})
    return True


if __name__ == "__main__":
    ok = main()
    if ok:
        print("[live] done")
    else:
        print("[live] failed")

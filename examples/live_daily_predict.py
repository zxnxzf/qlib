#!/usr/bin/env python3  # 指定解释器
"""
live_daily_predict：实盘两阶段入口，复用 daily_predict 但支持实时报价定价。
Phase1：T-1 特征跑模型+Topk，输出候选 symbols_req。
Phase2：注入当日 quotes_live，用实时报价算 shares/price，输出 orders_to_exec。
"""

import argparse  # 解析命令行参数（可选 --config）
import json  # 读取 JSON 配置 / state.json
import time  # 等待 state 用
from dataclasses import replace  # 覆盖配置的便捷方法
from pathlib import Path  # 路径处理
from typing import Dict, Optional  # 类型注解
import sys  # 修改 sys.path 便于导入

import pandas as pd  # 数据处理

from qlib.utils import get_pre_trading_date  # 获取前一交易日
from qlib.backtest.decision import OrderDir  # 订单方向枚举
from qlib.backtest.live_exchange import LiveExchange  # 实时报价 Exchange

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
        "weight_method": "equal"
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
    }
}
# ==============================================================


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
    return {str(r[code_col]).strip(): float(r[pos_col]) for _, r in df.iterrows()}


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
    records = {}
    for _, r in df.iterrows():
        code = str(r["code"]).strip()
        records[code] = {
            "last": float(r.get("last", float("nan"))),
            "bid1": float(r.get("bid1", float("nan"))) if "bid1" in df.columns else float("nan"),
            "ask1": float(r.get("ask1", float("nan"))) if "ask1" in df.columns else float("nan"),
            "high_limit": float(r.get("high_limit", float("nan"))) if "high_limit" in df.columns else float("nan"),
            "low_limit": float(r.get("low_limit", float("nan"))) if "low_limit" in df.columns else float("nan"),
        }
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
    while True:
        st = _read_state(path)
        if st.get("phase") == expect_phase and st.get("version") == expect_version:
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
                print(f"   [警告] 无法补全持仓价格: {err}")
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

    # ===== Phase0: 请求持仓，等待 positions_ready =====
    print(f"[live] 请求持仓，写 state=positions_needed, version={version}")
    _write_state(state_path, phase="positions_needed", version=version, extra={})
    print(f"[live] 等待 positions_ready (version={version}) ...")
    _wait_for_phase(state_path, expect_phase="positions_ready", expect_version=version, timeout=wait_secs)
    holdings = _read_positions(positions_path)
    quotes_live = _read_quotes(quotes_path)

    # 预测配置
    pred_cfg_raw = cfg.get("prediction", {})
    today = pd.Timestamp.utcnow().strftime("%Y-%m-%d")
    pred_date = pred_cfg_raw.get("prediction_date")
    if str(pred_date).lower() == "auto" or not pred_date:
        pred_date = get_pre_trading_date(today) or today
    base_pred_cfg = PredictionConfig(
        experiment_id=pred_cfg_raw.get("experiment_id", "866149675032491302"),
        recorder_id=pred_cfg_raw.get("recorder_id", "3d0e78192f384bb881c63b32f743d5f8"),
        prediction_date=pred_date,
        top_k=pred_cfg_raw.get("top_k", 20),
        min_score_threshold=pred_cfg_raw.get("min_score_threshold", 0.0),
        weight_method=pred_cfg_raw.get("weight_method", "equal"),
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

    # 构造实盘 Pipeline
    pipeline = LiveDailyPredictionPipeline(
        prediction_cfg=base_pred_cfg,
        trading_cfg=base_trading_cfg,
        output_cfg=output_cfg,
        quotes_live=quotes_live,
        enable_detailed_logs=True,
    )

    # Phase1：推理 + Topk 选股（假定 positions 已就绪）
    pipeline._init_environment()
    pipeline.recorder = pipeline._load_recorder()
    pipeline.model = pipeline._load_model(pipeline.recorder)
    pipeline.dataset = pipeline._build_dataset()

    preds = pipeline._generate_predictions()
    pred_df = pipeline._prepare_predictions(preds)
    pred_df = pipeline._attach_market_data(pred_df)
    pred_df = pipeline._select_buyable_topk(pred_df)

    symbols_path = Path(symbols_out)
    symbols_path.parent.mkdir(parents=True, exist_ok=True)
    symbols_path.write_text(
        pred_df[["instrument", "score", "target_weight"]].to_csv(index=False), encoding="utf-8-sig"
    )
    print(f"[live] symbols_req -> {symbols_path}")
    # 写 state：symbols_ready
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _write_state(state_path, phase="symbols_ready", version=version, extra={"symbols": symbols_path.name})

    # 等待 quotes_ready
    print(f"[live] 等待 quotes_ready (version={version}) ...")
    _wait_for_phase(state_path, expect_phase="quotes_ready", expect_version=version, timeout=args.wait_secs)
    quotes_live = _read_quotes(args.quotes)
    pipeline.quotes_live = quotes_live

    # Phase2：注入 quotes_live，用 LiveExchange 生成订单
    trading_result = pipeline._generate_trading_orders(pred_df)
    orders_df = trading_result.orders if trading_result else pd.DataFrame()
    orders_path = Path(args.orders_out)
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

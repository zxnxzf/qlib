#!/usr/bin/env python3
"""
Qlib实盘预测脚本 - 基于Qlib内置模块的每日预测流程。

该脚本展示如何使用Qlib的Recorder、Dataset、SignalRecord以及订单生成模块，
完成每日预测、交易信号生成与结果落地。
"""

import argparse
import json
import sys
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union

import numpy as np
import pandas as pd

import qlib
from qlib.constant import REG_CN
from qlib.workflow import R
from qlib.utils import get_pre_trading_date, init_instance_by_config
from qlib.workflow.record_temp import SignalRecord
from qlib.data import D
from qlib.backtest import Exchange
from qlib.backtest.position import Position
from qlib.backtest.decision import OrderDir
from qlib.contrib.strategy.order_generator import OrderGenWOInteract

warnings.filterwarnings("ignore")


def _default_output_dir() -> Path:
    script_dir = Path(__file__).resolve().parent
    return (script_dir.parent / "predictions").resolve()


def _load_current_holdings(source: Optional[str]) -> Dict[str, float]:
    """Load current holdings from a JSON string or JSON file path."""
    if not source:
        return {}
    source = source.strip()
    if not source:
        return {}

    path = Path(source).expanduser()
    if path.exists():
        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as err:
            raise ValueError(f"无法读取持仓文件 {path}: {err}") from err
    else:
        raw_text = source

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as err:
        raise ValueError(f"无法解析持仓信息，请提供 JSON 字典或可读文件: {err}") from err

    if not isinstance(parsed, dict):
        raise ValueError("持仓信息必须是股票代码到持仓数量的 JSON 字典格式")

    holdings: Dict[str, float] = {}
    for code, amount in parsed.items():
        try:
            holdings[str(code)] = float(amount)
        except (TypeError, ValueError) as err:
            raise ValueError(f"持仓数量必须是可转换为浮点数的值: {code} -> {amount}") from err
    return holdings


@dataclass
class PredictionConfig:
    experiment_id: str
    recorder_id: str
    prediction_date: str
    provider_uri: str = "~/.qlib/qlib_data/cn_data"
    region: str = REG_CN
    top_k: int = 50
    min_score_threshold: float = 0.0
    weight_method: str = "equal"
    dataset_start: Optional[str] = "2020-01-01"
    min_history_days: int = 120
    instruments: Union[Sequence[str], str] = "csi300"
    dataset_class: str = "DatasetH"
    dataset_module: str = "qlib.data.dataset"
    handler_class: str = "Alpha158"
    handler_module: str = "qlib.contrib.data.handler"
    segment: str = "test"
    handler_kwargs: Dict[str, object] = field(default_factory=dict)

    def dataset_config(self, history_days: Optional[int] = None) -> Dict[str, object]:
        if history_days is None:
            history_days = self.min_history_days
        history_days = max(int(history_days or 0), 0)
        base_start = pd.Timestamp(self.prediction_date) - pd.Timedelta(days=history_days)
        if self.dataset_start:
            earliest_start = pd.Timestamp(self.dataset_start)
            effective_start = max(base_start, earliest_start)
        else:
            effective_start = base_start
        start_str = effective_start.strftime("%Y-%m-%d")

        handler_kwargs = {
            "start_time": start_str,
            "end_time": self.prediction_date,
            "instruments": self.instruments,
            "fit_start_time": start_str,
            "fit_end_time": self.prediction_date,
        }
        handler_kwargs.update(self.handler_kwargs or {})
        return {
            "class": self.dataset_class,
            "module_path": self.dataset_module,
            "kwargs": {
                "handler": {
                    "class": self.handler_class,
                    "module_path": self.handler_module,
                    "kwargs": handler_kwargs,
                },
                "segments": {
                    self.segment: (self.prediction_date, self.prediction_date),
                },
            },
        }


@dataclass
class TradingConfig:
    enable_trading: bool = True
    use_exchange_system: bool = True
    total_cash: float = 100000.0
    min_shares: int = 100
    risk_degree: float = 0.95
    price_search_days: int = 5
    trade_freq: str = "day"
    deal_price: str = "close"
    current_holdings: Dict[str, float] = field(default_factory=dict)


@dataclass
class OutputConfig:
    output_dir: Path = field(default_factory=_default_output_dir)
    encoding: str = "utf-8-sig"

    def ensure_directory(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)


@dataclass
class TradingResult:
    orders: pd.DataFrame
    total_buy_amount: float
    total_sell_amount: float
    net_amount: float
    price_success_rate: float
    tradable_stocks: int
    target_shares: Dict[str, int]
    exchange_used: bool = True

    @property
    def has_orders(self) -> bool:
        return not self.orders.empty

    @property
    def buy_orders(self) -> pd.DataFrame:
        return self.orders[self.orders["action"] == "买入"] if self.has_orders else self.orders

    @property
    def sell_orders(self) -> pd.DataFrame:
        return self.orders[self.orders["action"] == "卖出"] if self.has_orders else self.orders

    @staticmethod
    def empty(price_success_rate: float = 0.0, tradable_stocks: int = 0, exchange_used: bool = False) -> "TradingResult":
        columns = ["order_id", "stock", "action", "shares", "price", "amount", "score", "weight"]
        empty_df = pd.DataFrame(columns=columns)
        return TradingResult(
            orders=empty_df,
            total_buy_amount=0.0,
            total_sell_amount=0.0,
            net_amount=0.0,
            price_success_rate=price_success_rate,
            tradable_stocks=tradable_stocks,
            target_shares={},
            exchange_used=exchange_used,
        )


class DailyPredictionPipeline:
    def __init__(
        self,
        prediction_cfg: PredictionConfig,
        trading_cfg: TradingConfig,
        output_cfg: OutputConfig,
        enable_detailed_logs: bool = True,
    ):
        self.prediction_cfg = prediction_cfg
        self.trading_cfg = trading_cfg
        self.output_cfg = output_cfg
        self.enable_detailed_logs = enable_detailed_logs

        self.recorder = None
        self.model = None
        self.dataset = None
        self.saved_files: List[Path] = []
        self.target_prediction_date = prediction_cfg.prediction_date
        self.effective_prediction_date = prediction_cfg.prediction_date

    def run(self) -> bool:
        print("[Qlib] Qlib实盘预测系统启动")
        print("=" * 50)
        self._print_config()

        try:
            self._init_environment()
            recorder = self._load_recorder()
            model = self._load_model(recorder)
            if model is None:
                return False

            self.recorder = recorder
            self.model = model
            self.dataset = self._build_dataset()

            predictions = self._generate_predictions()
            pred_df = self._prepare_predictions(predictions)
            pred_df = self._attach_market_data(pred_df)

            trading_result = self._generate_trading_orders(pred_df)

            self.saved_files, final_pred_df = self._save_outputs(pred_df, trading_result)
            self._log_summary(final_pred_df, trading_result)
            return True
        except Exception as err:  # pragma: no cover - 用于调试
            print(f"[错误] 系统执行失败: {err}")
            if self.enable_detailed_logs:
                import traceback

                traceback.print_exc()
            return False

    # ------------------------------------------------------------------ #
    # 初始化与资源加载
    # ------------------------------------------------------------------ #
    def _print_config(self) -> None:
        cfg = self.prediction_cfg
        trading = self.trading_cfg
        print("[配置] 预测配置:")
        print(f"   目标预测日: {self.target_prediction_date}")
        if self.effective_prediction_date != self.target_prediction_date:
            print(f"   使用数据日: {self.effective_prediction_date}")
        print(f"   实验ID: {cfg.experiment_id}")
        print(f"   推荐股票数: {cfg.top_k}")
        print("[交易] 交易配置:")
        print(f"   总资金: {trading.total_cash:,.0f} 元")
        print(f"   最小股数: {trading.min_shares}")
        print(f"   Exchange系统: {'启用' if trading.use_exchange_system else '禁用'}")
        print(f"   交易指令: {'启用' if trading.enable_trading else '禁用'}")

    def _init_environment(self) -> None:
        print("\n[环境] 初始化Qlib环境...")
        self.output_cfg.ensure_directory()
        qlib.init(
            provider_uri=self.prediction_cfg.provider_uri,
            region=self.prediction_cfg.region,
            kernels=1,
            joblib_backend="threading",
            maxtasksperchild=1,
        )
        self._align_prediction_date()
        print("[成功] Qlib环境初始化成功")

    def _align_prediction_date(self) -> None:
        freq = self.trading_cfg.trade_freq or "day"
        effective = pd.Timestamp(self.target_prediction_date)
        visited: Set[str] = set()
        while True:
            current = effective.strftime("%Y-%m-%d")
            if current in visited:
                raise ValueError(
                    f"无法为 {self.target_prediction_date} 定位可用交易日，请检查数据目录"
                )
            visited.add(current)
            calendar = D.calendar(start_time=current, end_time=current, freq=freq)
            if calendar:
                break
            try:
                previous = get_pre_trading_date(current)
                if isinstance(previous, pd.Timestamp):
                    previous = previous.strftime("%Y-%m-%d")
            except ValueError:
                history = D.calendar(end_time=current, freq=freq)
                history = [pd.Timestamp(d) for d in history if pd.Timestamp(d) < pd.Timestamp(current)]
                previous = history[-1].strftime("%Y-%m-%d") if history else None
            if not previous:
                raise ValueError(
                    f"数据目录中不存在 {self.target_prediction_date} 及之前的交易日，请更新行情"
                )
            print(f"[警告] {current} 缺少行情数据，回退使用前一交易日 {previous}")
            effective = pd.Timestamp(previous)
        effective_str = effective.strftime("%Y-%m-%d")
        self.effective_prediction_date = effective_str
        self.prediction_cfg.prediction_date = effective_str

    def _load_recorder(self):
        print("\n[模型] 加载训练记录器...")
        recorder = R.get_recorder(
            experiment_id=self.prediction_cfg.experiment_id,
            recorder_id=self.prediction_cfg.recorder_id,
        )
        print("[成功] Recorder获取成功")
        return recorder

    def _load_model(self, recorder) -> Optional[object]:
        print("   尝试加载模型文件...")
        candidates = ["params.pkl", "model.pkl", "trained_model.pkl", "lgb_model.pkl"]
        for name in candidates:
            try:
                model = recorder.load_object(name)
                print(f"[成功] 模型加载成功: {name}")
                return model
            except Exception as err:
                print(f"   [警告] 无法加载 {name}: {err}")
        print(f"[错误] 无法从Recorder加载模型，请确认存在以下文件之一: {candidates}")
        return None

    def _build_dataset(self):
        print("\n[数据] 初始化Dataset实例...")
        history_candidates = []
        default_window = self.prediction_cfg.min_history_days
        if default_window is not None:
            history_candidates.append(default_window)
        history_candidates.extend([90, 60, 30])
        seen = set()
        for window in history_candidates:
            if window in seen:
                continue
            seen.add(window)
            dataset_cfg = self.prediction_cfg.dataset_config(history_days=window)
            handler_kwargs = dataset_cfg.get("kwargs", {}).get("handler", {}).get("kwargs", {})
            start_time = handler_kwargs.get("start_time")
            end_time = handler_kwargs.get("end_time")
            try:
                dataset = init_instance_by_config(dataset_cfg)
            except MemoryError:
                print(f"[警告] 数据集窗口 {window} 天内存不足，尝试更短窗口...")
                continue
            if start_time and end_time:
                print(f"[成功] 数据集配置完成: {start_time} -> {end_time}")
            else:
                print("[成功] 数据集配置完成")
            if window != default_window:
                self.prediction_cfg.min_history_days = window
            return dataset
        raise MemoryError("数据集初始化失败：所有窗口长度均触发内存不足，可尝试进一步减小样本范围。")

    # ------------------------------------------------------------------ #
    # 预测与结果整理
    # ------------------------------------------------------------------ #
    def _generate_predictions(self) -> pd.DataFrame:
        print("\n[预测] 执行预测...")
        signal_record = SignalRecord(model=self.model, dataset=self.dataset, recorder=self.recorder)
        signal_record.generate()
        predictions = signal_record.load("pred.pkl")
        if isinstance(predictions, pd.Series):
            predictions = predictions.to_frame("score")
        print(f"[成功] 预测完成: {predictions.shape}")
        if predictions is not None and not predictions.empty:
            values = predictions.to_numpy().ravel()
            print(f"   分数范围: {values.min():.4f} ~ {values.max():.4f}")
        return predictions

    def _prepare_predictions(self, predictions: pd.DataFrame) -> pd.DataFrame:
        print("\n[处理] 处理预测结果...")
        if predictions is None or predictions.empty:
            print("[警告] 预测结果为空")
            columns = [
                "instrument",
                "datetime",
                "score",
                "target_weight",
                "prediction_date",
                "generated_time",
            ]
            return pd.DataFrame(columns=columns)

        df = predictions.reset_index()
        index_names = predictions.index.names or []
        for col_name, idx_name in zip(df.columns, index_names):
            if idx_name:
                df = df.rename(columns={col_name: idx_name})

        if "instrument" not in df.columns and len(df.columns) >= 2:
            df = df.rename(columns={df.columns[1]: "instrument"})
        if "datetime" not in df.columns and len(df.columns) >= 1:
            df = df.rename(columns={df.columns[0]: "datetime"})

        score_col = df.columns[-1]
        df = df.rename(columns={score_col: "score"})

        df = df[["instrument", "datetime", "score"]].copy()
        df["instrument"] = df["instrument"].astype(str)
        df["datetime"] = pd.to_datetime(df["datetime"]).dt.strftime("%Y-%m-%d")
        df = df.dropna(subset=["score"])

        if self.prediction_cfg.min_score_threshold > 0:
            df = df[df["score"] >= self.prediction_cfg.min_score_threshold]

        df = df.sort_values("score", ascending=False)
        if self.prediction_cfg.top_k > 0:
            df = df.head(self.prediction_cfg.top_k)

        df["target_weight"] = self._compute_weights(df["score"], self.prediction_cfg.weight_method)
        df["prediction_date"] = self.target_prediction_date
        df["generated_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[成功] 结果处理完成: {len(df)} 只股票")
        return df.reset_index(drop=True)

    @staticmethod
    def _compute_weights(scores: pd.Series, method: str) -> pd.Series:
        if scores.empty:
            return pd.Series(dtype=float)
        method = (method or "equal").lower()
        if method == "equal":
            weights = np.full(len(scores), 1.0 / len(scores))
        elif method == "score":
            score_sum = float(scores.sum())
            if score_sum <= 0:
                weights = np.full(len(scores), 1.0 / len(scores))
            else:
                weights = scores / score_sum
        else:
            print(f"[警告] 未知权重方法 {method}，使用等权重")
            weights = np.full(len(scores), 1.0 / len(scores))
        return pd.Series(weights, index=scores.index, dtype=float)

    def _attach_market_data(self, pred_df: pd.DataFrame) -> pd.DataFrame:
        if pred_df.empty:
            pred_df["price"] = pd.Series(dtype=float)
            pred_df["is_tradable"] = pd.Series(dtype=bool)
            return pred_df

        prices = self._fetch_prices(pred_df["instrument"].tolist())
        merged = pred_df.merge(prices, on="instrument", how="left")
        merged["price"] = merged["price"].astype(float)
        merged["is_tradable"] = merged["price"].notna() & (merged["price"] > 0)
        return merged

    def _fetch_prices(self, instruments: Sequence[str]) -> pd.DataFrame:
        if not instruments:
            return pd.DataFrame(columns=["instrument", "price"])

        tried_dates = set()
        search_date = pd.Timestamp(self.prediction_cfg.prediction_date)
        max_attempts = max(self.trading_cfg.price_search_days, 0)
        attempt = 0
        while attempt <= max_attempts:
            date_str = search_date.strftime("%Y-%m-%d")
            try:
                data = D.features(
                    instruments,
                    ["$close"],
                    start_time=date_str,
                    end_time=date_str,
                    freq=self.trading_cfg.trade_freq,
                )
            except Exception:
                data = None

            if data is not None and not data.empty:
                price_df = (
                    data.reset_index()
                    .rename(columns={"$close": "price"})
                    .loc[:, ["instrument", "price"]]
                    .drop_duplicates(subset="instrument")
                )
                return price_df

            attempt += 1
            prev_date = get_pre_trading_date(date_str)
            if not prev_date or prev_date in tried_dates:
                break
            tried_dates.add(prev_date)
            search_date = pd.Timestamp(prev_date)

        return pd.DataFrame({"instrument": instruments, "price": [np.nan] * len(instruments)})

    # ------------------------------------------------------------------ #
    # 交易指令生成
    # ------------------------------------------------------------------ #
    def _generate_trading_orders(self, pred_df: pd.DataFrame) -> TradingResult:
        if not self.trading_cfg.enable_trading:
            print("\n[交易] 交易指令生成功能已禁用")
            price_rate, tradable = self._price_stats(pred_df)
            return TradingResult.empty(price_success_rate=price_rate, tradable_stocks=tradable, exchange_used=False)

        if not self.trading_cfg.use_exchange_system:
            print("\n[交易] Exchange系统禁用，跳过交易指令生成")
            price_rate, tradable = self._price_stats(pred_df)
            return TradingResult.empty(price_success_rate=price_rate, tradable_stocks=tradable, exchange_used=False)

        print("\n[交易] 使用Qlib交易模块生成指令...")
        if pred_df.empty:
            print("   [警告] 预测结果为空，跳过交易指令生成")
            return TradingResult.empty(exchange_used=True)

        price_rate, tradable_total = self._price_stats(pred_df)
        tradable_df = pred_df[pred_df.get("is_tradable", False)].copy()
        if tradable_df.empty:
            print("   [警告] 没有可交易的股票（缺少价格或不可交易）")
            return TradingResult.empty(price_success_rate=price_rate, tradable_stocks=0, exchange_used=True)

        stock_pool = tradable_df["instrument"].tolist()
        trade_start = pd.Timestamp(self.prediction_cfg.prediction_date)
        trade_end = trade_start + pd.Timedelta(days=1)
        start_date = (trade_start - pd.Timedelta(days=max(self.trading_cfg.price_search_days, 1))).strftime("%Y-%m-%d")
        exchange = Exchange(
            codes=stock_pool,
            start_time=start_date,
            end_time=trade_end.strftime("%Y-%m-%d"),
            deal_price=self.trading_cfg.deal_price,
            freq=self.trading_cfg.trade_freq,
        )

        position = Position(
            cash=self.trading_cfg.total_cash,
            position_dict={code: {"amount": amount} for code, amount in self.trading_cfg.current_holdings.items()},
        )
        if self.trading_cfg.current_holdings:
            try:
                position.fill_stock_value(start_time=self.prediction_cfg.prediction_date, freq=self.trading_cfg.trade_freq)
            except Exception as err:
                print(f"   [警告] 无法补全持仓价格信息: {err}")

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
            print("   [警告] 订单生成结果为空")
            return TradingResult.empty(price_success_rate=price_rate, tradable_stocks=len(tradable_df), exchange_used=True)

        orders_df, total_buy_amount, total_sell_amount = self._orders_to_frame(
            orders=orders,
            exchange=exchange,
            base_df=tradable_df,
            trade_start=trade_start,
            trade_end=trade_end,
        )
        target_shares = self._calculate_target_shares(exchange, target_weight_position, trade_start, trade_end)
        net_amount = total_buy_amount - total_sell_amount

        print(f"   生成买入指令: {len(orders_df[orders_df['action'] == '买入'])} 条")
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

    def _price_stats(self, pred_df: pd.DataFrame) -> Tuple[float, int]:
        if "is_tradable" not in pred_df.columns or pred_df.empty:
            return 0.0, 0
        return float(pred_df["is_tradable"].mean()), int(pred_df["is_tradable"].sum())

    def _calculate_target_shares(
        self,
        exchange: Exchange,
        target_weight_position: Dict[str, float],
        trade_start: pd.Timestamp,
        trade_end: pd.Timestamp,
    ) -> Dict[str, int]:
        try:
            amount_dict = exchange.generate_amount_position_from_weight_position(
                weight_position=target_weight_position,
                cash=self.trading_cfg.total_cash * self.trading_cfg.risk_degree,
                start_time=trade_start,
                end_time=trade_end,
            )
        except Exception:
            amount_dict = {code: 0.0 for code in target_weight_position}

        for code, amount in self.trading_cfg.current_holdings.items():
            amount_dict[code] = amount_dict.get(code, 0.0) + float(amount)

        return {code: int(round(value)) for code, value in amount_dict.items()}

    def _orders_to_frame(
        self,
        orders: List[object],
        exchange: Exchange,
        base_df: pd.DataFrame,
        trade_start: pd.Timestamp,
        trade_end: pd.Timestamp,
    ) -> Tuple[pd.DataFrame, float, float]:
        rows = []
        total_buy_amount = 0.0
        total_sell_amount = 0.0

        for order in orders:
            shares = int(round(order.amount))
            if shares <= 0:
                continue
            if shares < self.trading_cfg.min_shares:
                continue

            price = self._resolve_price(exchange, order, trade_start, trade_end, base_df)
            amount = float(shares * price) if pd.notna(price) else 0.0

            direction = "买入" if order.direction == OrderDir.BUY else "卖出"
            if direction == "买入":
                total_buy_amount += amount
            else:
                total_sell_amount += amount

            stock_row = base_df[base_df["instrument"] == order.stock_id]
            score = float(stock_row["score"].iloc[0]) if not stock_row.empty else np.nan
            weight = float(stock_row["target_weight"].iloc[0]) if not stock_row.empty else 0.0

            rows.append(
                {
                    "order_id": f"{order.stock_id}_{'BUY' if direction == '买入' else 'SELL'}_{trade_start.strftime('%Y%m%d')}",
                    "stock": order.stock_id,
                    "action": direction,
                    "shares": shares,
                    "price": float(price) if pd.notna(price) else np.nan,
                    "amount": amount,
                    "score": score,
                    "weight": weight,
                }
            )

        orders_df = pd.DataFrame(
            rows,
            columns=["order_id", "stock", "action", "shares", "price", "amount", "score", "weight"],
        )
        return orders_df, float(total_buy_amount), float(total_sell_amount)

    def _resolve_price(
        self,
        exchange: Exchange,
        order,
        trade_start: pd.Timestamp,
        trade_end: pd.Timestamp,
        base_df: pd.DataFrame,
    ) -> float:
        price = np.nan
        try:
            price = exchange.get_deal_price(
                stock_id=order.stock_id,
                start_time=trade_start,
                end_time=trade_end,
                direction=order.direction,
            )
        except Exception:
            price = np.nan

        if pd.isna(price) or price <= 0:
            try:
                price = exchange.get_close(order.stock_id, start_time=trade_start, end_time=trade_end)
            except Exception:
                price = np.nan

        if (pd.isna(price) or price <= 0) and base_df is not None:
            row = base_df[base_df["instrument"] == order.stock_id]
            if not row.empty:
                candidate = row["price"].iloc[0]
                if pd.notna(candidate) and candidate > 0:
                    price = float(candidate)

        return float(price) if pd.notna(price) and price > 0 else np.nan

    # ------------------------------------------------------------------ #
    # 结果保存与输出
    # ------------------------------------------------------------------ #
    def _save_outputs(self, pred_df: pd.DataFrame, trading_result: TradingResult) -> Tuple[List[Path], pd.DataFrame]:
        print("\n[保存] 保存预测结果...")
        self.output_cfg.ensure_directory()
        date_tag = self.target_prediction_date.replace("-", "")

        final_pred_df = pred_df.copy()
        if trading_result and trading_result.target_shares:
            final_pred_df["target_shares"] = (
                final_pred_df["instrument"].map(trading_result.target_shares).fillna(0).astype(int)
            )
        else:
            final_pred_df["target_shares"] = 0

        columns_order = [
            "instrument",
            "datetime",
            "score",
            "target_weight",
            "price",
            "prediction_date",
            "generated_time",
            "target_shares",
            "is_tradable",
        ]
        available_columns = [col for col in columns_order if col in final_pred_df.columns]
        final_pred_df = final_pred_df[available_columns]

        saved_files: List[Path] = []

        csv_path = self.output_cfg.output_dir / f"prediction_results_{date_tag}.csv"
        self._write_prediction_csv(final_pred_df, csv_path)
        saved_files.append(csv_path)

        excel_path = self.output_cfg.output_dir / f"prediction_results_{date_tag}.xlsx"
        self._write_prediction_excel(final_pred_df, excel_path)
        saved_files.append(excel_path)

        if trading_result and trading_result.has_orders:
            orders_path = self.output_cfg.output_dir / f"trading_orders_{date_tag}.csv"
            trading_result.orders.to_csv(orders_path, index=False, encoding=self.output_cfg.encoding)
            saved_files.append(orders_path)

            target_path = self.output_cfg.output_dir / f"target_position_{date_tag}.csv"
            final_pred_df.to_csv(target_path, index=False, encoding=self.output_cfg.encoding)
            saved_files.append(target_path)

        log_path = self.output_cfg.output_dir / f"prediction_log_{date_tag}.txt"
        saved_files.append(log_path)
        self._write_log(final_pred_df, trading_result, saved_files, log_path)

        print("[成功] 文件保存完成")
        for path in saved_files:
            print(f"   [文件] {path}")

        return saved_files, final_pred_df

    def _write_prediction_csv(self, df: pd.DataFrame, path: Path) -> None:
        df.to_csv(path, index=False, encoding=self.output_cfg.encoding)

    def _write_prediction_excel(self, df: pd.DataFrame, path: Path) -> None:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="预测结果", index=False)
            stats_df = pd.DataFrame(
                {
                    "指标": ["预测数量", "平均分数", "分数标准差", "最高分数", "最低分数", "权重总和"],
                    "数值": [
                        len(df),
                        df["score"].mean() if not df.empty else 0.0,
                        df["score"].std() if len(df) > 1 else 0.0,
                        df["score"].max() if not df.empty else 0.0,
                        df["score"].min() if not df.empty else 0.0,
                        df["target_weight"].sum() if "target_weight" in df.columns else 0.0,
                    ],
                }
            )
            stats_df.to_excel(writer, sheet_name="统计信息", index=False)

    def _write_log(
        self,
        pred_df: pd.DataFrame,
        trading_result: TradingResult,
        saved_files: List[Path],
        path: Path,
    ) -> None:
        lines: List[str] = []
        lines.append("Qlib实盘预测交易系统执行日志")
        lines.append("=" * 60)
        lines.append(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"目标预测日: {self.target_prediction_date}")
        lines.append(f"使用数据日: {self.effective_prediction_date}")
        lines.append(f"实验ID: {self.prediction_cfg.experiment_id}")
        lines.append(f"记录ID: {self.prediction_cfg.recorder_id}")
        lines.append("")

        lines.append("[系统配置]")
        lines.append(f"总资金: {self.trading_cfg.total_cash:,.0f}元")
        lines.append(f"推荐股票数: {self.prediction_cfg.top_k}")
        lines.append(f"权重方法: {self.prediction_cfg.weight_method}")
        lines.append(f"交易指令: {'启用' if self.trading_cfg.enable_trading else '禁用'}")
        lines.append("")

        lines.append("[预测结果]")
        lines.append(f"预测股票数: {len(pred_df)}")
        if not pred_df.empty:
            lines.append(f"平均分数: {pred_df['score'].mean():.6f}")
            lines.append(f"分数标准差: {pred_df['score'].std():.6f}" if len(pred_df) > 1 else "分数标准差: 0.000000")
            lines.append(f"分数范围: {pred_df['score'].min():.6f} ~ {pred_df['score'].max():.6f}")
            if "target_weight" in pred_df:
                lines.append(f"权重总和: {pred_df['target_weight'].sum():.6f}")
        else:
            lines.append("无预测记录")
        lines.append("")

        lines.append("[交易执行结果]")
        if trading_result and trading_result.exchange_used:
            lines.append(f"可交易股票数: {trading_result.tradable_stocks}")
            lines.append(f"价格获取成功率: {trading_result.price_success_rate:.1%}")
            lines.append(f"买入指令数: {len(trading_result.buy_orders)}")
            lines.append(f"卖出指令数: {len(trading_result.sell_orders)}")
            lines.append(f"预计买入金额: {trading_result.total_buy_amount:,.0f}元")
            lines.append(f"预计卖出金额: {trading_result.total_sell_amount:,.0f}元")
            lines.append(f"净交易金额: {trading_result.net_amount:,.0f}元")
            lines.append("")
            lines.append("[交易指令] 详情（前10条）:")
            if trading_result.has_orders:
                for idx, row in trading_result.orders.head(10).iterrows():
                    price_info = f"{row['price']:.2f}" if pd.notna(row["price"]) else "NaN"
                    lines.append(
                        f"{idx + 1:2d}. {row['stock']} | {row['action']} | {int(row['shares'])}股 | "
                        f"{price_info}元 | {row['amount']:.0f}元 | 权重: {row['weight']:.4f}"
                    )
                if len(trading_result.orders) > 10:
                    lines.append(f"... 还有 {len(trading_result.orders) - 10} 条交易指令")
            else:
                lines.append("无交易指令")
        else:
            lines.append("未生成交易指令")
        lines.append("")

        lines.append("[Top 10] 推荐股票:")
        if pred_df.empty:
            lines.append("无候选股票")
        else:
            for idx, row in pred_df.head(10).iterrows():
                price_value = row.get("price", np.nan)
                if pd.notna(price_value) and price_value > 0:
                    price_info = f" | 价格: {price_value:.2f}元"
                else:
                    price_info = ""
                lines.append(
                    f"{idx + 1:2d}. {row['instrument']} | 分数: {row['score']:.6f} | "
                    f"权重: {row['target_weight']:.4f}{price_info}"
                )
        lines.append("")

        lines.append("[文件]")
        for file_path in saved_files:
            lines.append(str(file_path))
        lines.append("")

        lines.append("=" * 60)
        lines.append("注意：本系统基于历史数据，所有预测和交易指令仅供参考。")
        lines.append("实际投资请考虑市场风险和交易成本。")

        with path.open("w", encoding="utf-8") as fp:
            fp.write("\n".join(lines))

    # ------------------------------------------------------------------ #
    # 控制台摘要
    # ------------------------------------------------------------------ #
    def _log_summary(self, pred_df: pd.DataFrame, trading_result: TradingResult) -> None:
        print("\n[完成] 预测交易任务完成!")
        print("=" * 60)
        print("[预测摘要]:")
        print(f"   目标预测日: {self.target_prediction_date}")
        if self.effective_prediction_date != self.target_prediction_date:
            print(f"   使用数据日: {self.effective_prediction_date}")
        print(f"   预测股票数: {len(pred_df)}")
        if not pred_df.empty:
            print(f"   平均分数: {pred_df['score'].mean():.6f}")
            print(f"   分数范围: {pred_df['score'].min():.6f} ~ {pred_df['score'].max():.6f}")
        else:
            print("   无预测记录")

        print("\n[Top 5] 推荐:")
        if pred_df.empty:
            print("   无候选股票")
        else:
            for idx, row in pred_df.head(5).iterrows():
                price_val = row.get("price", np.nan)
                if pd.notna(price_val) and price_val > 0:
                    price_info = f" | 价格: {price_val:.2f}元"
                else:
                    price_info = ""
                print(
                    f"   {idx + 1}. {row['instrument']} | 分数: {row['score']:.6f} | "
                    f"权重: {row['target_weight']:.4f}{price_info}"
                )

        if trading_result and trading_result.exchange_used:
            print("\n[交易执行结果]:")
            print(f"   价格获取成功率: {trading_result.price_success_rate:.1%}")
            print(f"   可交易股票数: {trading_result.tradable_stocks}")
            print(f"   买入指令: {len(trading_result.buy_orders)} 条")
            print(f"   预计交易金额: {trading_result.total_buy_amount:,.0f} 元")
            if self.trading_cfg.total_cash > 0:
                print(f"   资金使用率: {trading_result.total_buy_amount / self.trading_cfg.total_cash:.1%}")
            if trading_result.has_orders:
                print("\n[交易指令] 样例（前3条）:")
                for idx, row in trading_result.orders.head(3).iterrows():
                    price_val = row["price"] if pd.notna(row["price"]) else 0.0
                    print(
                        f"   {idx + 1}. {row['stock']:8s} | {int(row['shares']):6d}股 | "
                        f"{price_val:8.2f}元 | {row['amount']:8.0f}元"
                    )
        else:
            print("\n[交易执行结果]:")
            print("   未生成交易指令")

        print("\n[文件] 文件输出:")
        for path in self.saved_files:
            size = path.stat().st_size if path.exists() else 0
            print(f"   [文件] {path.name} ({size:,} bytes)")

        print("\n[系统状态]:")
        model_name = type(self.model).__name__ if self.model is not None else "未知"
        print(f"   预测模型: {model_name}")
        trade_mode = "专业模式" if trading_result and trading_result.exchange_used else "简化模式"
        print(f"   交易系统: {trade_mode}")
        data_quality = "良好" if trading_result and trading_result.price_success_rate > 0.7 else "需要改善"
        print(f"   数据质量: {data_quality}")

        print("\n[成功] 系统执行完成！生成的文件可用于量化交易系统")
        print("[警告] 重要提示：所有预测和交易指令仅供参考，投资有风险，请谨慎决策")


def main(argv: Optional[Sequence[str]] = None) -> bool:
    parser = argparse.ArgumentParser(description="Qlib 日常预测脚本")
    parser.add_argument(
        "--holdings",
        dest="holdings",
        type=str,
        default=None,
        help='当前持仓信息，支持 JSON 字符串或 JSON 文件路径，如 {"SH600519": 1000}',
    )
    args = parser.parse_args(argv)

    try:
        current_holdings = _load_current_holdings(args.holdings)
    except ValueError as err:
        print(f"[错误] {err}")
        return False

    if current_holdings:
        print(f"[参数] 当前持仓条目: {len(current_holdings)} | {list(current_holdings)[:3]}")

    prediction_cfg = PredictionConfig(
        experiment_id="866149675032491302",
        recorder_id="3d0e78192f384bb881c63b32f743d5f8",
        prediction_date="2025-10-15",
        top_k=10,
        min_score_threshold=0.0,
        weight_method="equal",
    )

    trading_cfg = TradingConfig(
        enable_trading=True,
        use_exchange_system=True,
        total_cash=100000,
        min_shares=100,
        price_search_days=5,
        current_holdings=current_holdings,
    )

    output_cfg = OutputConfig()

    pipeline = DailyPredictionPipeline(
        prediction_cfg=prediction_cfg,
        trading_cfg=trading_cfg,
        output_cfg=output_cfg,
        enable_detailed_logs=True,
    )
    return pipeline.run()


if __name__ == "__main__":
    success = main()
    if success:
        print("\n[完成] 预测成功完成!")
        sys.exit(0)
    else:
        print("\n[错误] 预测执行失败!")
        sys.exit(1)

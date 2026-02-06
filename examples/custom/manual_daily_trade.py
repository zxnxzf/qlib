#!/usr/bin/env python3
"""
Manual daily trading script for Qlib.

Key behavior:
- Use local weekday calendar to decide if today is a trading day.
- Use last available Qlib data date as pred_date (T-1).
- Generate orders via Exchange + OrderGenWOInteract.
- Output adjusted and raw (unadjusted) price/share columns for manual execution.
"""

import argparse
import copy
import json
import math
import shutil
import tarfile
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union
from urllib.request import urlretrieve

import numpy as np
import pandas as pd

import sys

# Ensure local repo import precedence.
_EXAMPLES_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _EXAMPLES_DIR.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import qlib
from qlib.constant import REG_CN
from qlib.data import D
from qlib.utils import get_pre_trading_date, init_instance_by_config
from qlib.workflow import R
from qlib.workflow.record_temp import SignalRecord
from qlib.backtest import Exchange
from qlib.backtest.utils import epsilon_change
from qlib.backtest.decision import Order, OrderDir
from qlib.backtest.position import Position


DEFAULT_CONFIG = {
    "qlib_init": {
        "provider_uri": "~/.qlib/qlib_data/cn_data",
        "region": "cn",
        "kernels": 1,
        "joblib_backend": "threading",
        "maxtasksperchild": 1,
    },
    "calendar": {
        "path": "trade_calendar_base.csv",
    },
    "runtime": {
        "trade_date": "auto",
    },
    "paths": {
        "positions": "positions_manual.csv",
        "orders_out": "orders_manual.csv",
        "holdings_history": "holdings_history_manual.json",
        "mlruns_uri": "auto",
        "positions_next": "positions_manual_next.csv",
    },
    "workflow_alignment": {
        "enabled": True,
        "mode": "align",
        "market": "all",
        "handler": {
            "start_time": "2020-01-01",
            "fit_start_time": "2020-01-01",
            "fit_end_time": "2022-12-31",
        },
        "use_required_pred_date": True,
        "use_position_count": True,
        "use_recorder_pred": True,
        "use_backtest_window": True,
        "use_execution_simulator": True,
    },
    "strategy": {
        "enabled": True,
        "top_k": 50,
        "n_drop": 2,
        "method_sell": "bottom",
        "method_buy": "top",
        "hold_thresh": 2,
        "only_tradable": None,
        "forbid_all_trade_at_limit": True,
    },
    "prediction": {
        "experiment_id": "1",
        "recorder_id": "e45264e7bf9348a28829a6089f06153c",
        "prediction_date": "auto",
        "top_k": 50,
        "min_score_threshold": 0.0,
        "weight_method": "equal",
        "pred_date_search_days": 10,
        "provider_uri": "~/.qlib/qlib_data/cn_data",
        "region": "cn",
        "instruments": "csi300",
        "dataset_class": "DatasetH",
        "dataset_module": "qlib.data.dataset",
        "handler_class": "Alpha158",
        "handler_module": "qlib.contrib.data.handler",
        "min_history_days": 120,
        "dataset_start": "2020-01-01",
        "segment": "test",
        "handler_kwargs": {},
    },
    "trading": {
        "total_cash": 0.0,
        "risk_degree": 0.95,
        "min_shares": 100,
        "price_search_days": 5,
        "trade_freq": "day",
        "deal_price": "close",
        "open_cost": 0.0005,
        "close_cost": 0.0015,
        "min_cost": 5.0,
        "impact_cost": 0.0,
        "limit_threshold": 0.095,
        "hold_thresh": 2,
        "only_tradable": False,
    },
    "data_update": {
        "enable_auto_update": True,
        "data_source_url": "https://ghfast.top/https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz",
        "data_source_urls": [
            "https://ghfast.top/https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz",
            "https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz",
        ],
        "download_timeout": 600,
        "retry_count": 3,
        "retry_interval": 10,
        "temp_dir": None,
    },
    "output": {
        "encoding": "utf-8-sig",
    },
}


@dataclass
class PredictionConfig:
    experiment_id: str
    recorder_id: str
    prediction_date: str
    provider_uri: str = "~/.qlib/qlib_data/cn_data"
    region: str = REG_CN
    top_k: int = 20
    min_score_threshold: float = 0.0
    weight_method: str = "equal"
    pred_date_search_days: int = 10
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
    total_cash: float = 0.0
    risk_degree: float = 0.95
    min_shares: int = 100
    price_search_days: int = 5
    trade_freq: str = "day"
    deal_price: str = "close"
    open_cost: float = 0.0005
    close_cost: float = 0.0015
    min_cost: float = 5.0
    impact_cost: float = 0.0
    limit_threshold: Optional[Union[Tuple[str, str], float]] = 0.095
    hold_thresh: int = 1
    only_tradable: bool = False


@dataclass
class DataUpdateConfig:
    enable_auto_update: bool = True
    data_source_url: str = ""
    data_source_urls: Optional[List[str]] = None
    download_timeout: int = 600
    retry_count: int = 3
    retry_interval: int = 10
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


def _resolve_path(path_str: str) -> Path:
    if not path_str:
        return Path("")
    path = Path(path_str).expanduser()
    if path.is_absolute():
        return path
    return (_EXAMPLES_DIR / path).resolve()


def _resolve_mlruns_uri(value: str) -> str:
    if not value or str(value).lower() == "auto":
        path = (_EXAMPLES_DIR.parent / "mlruns").resolve()
        return f"file:{path}"
    value_str = str(value)
    if value_str.startswith("file:"):
        return value_str
    if "://" in value_str:
        return value_str
    path = Path(value_str).expanduser()
    if not path.is_absolute():
        path = (_EXAMPLES_DIR / path).resolve()
    return f"file:{path}"


def _missing_required_data(data_path: Path, instruments) -> List[str]:
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


def _is_data_outdated(data_path: Path, required_date: str, instruments) -> Tuple[bool, Optional[str]]:
    latest_local = _latest_calendar_date(data_path)
    if latest_local is None:
        return True, None
    if required_date:
        try:
            if pd.Timestamp(latest_local) < pd.Timestamp(required_date):
                return True, latest_local
        except Exception:
            if latest_local < required_date:
                return True, latest_local
    if _missing_required_data(data_path, instruments):
        return True, latest_local
    return False, latest_local


def _download_and_update_data(cfg: DataUpdateConfig, target_path: Path) -> bool:
    temp_dir = cfg.get_temp_dir()
    download_path = temp_dir / "qlib_bin.tar.gz"
    extract_dir = temp_dir / "extracted"
    retry_count = max(int(cfg.retry_count), 1)
    retry_interval = max(int(cfg.retry_interval), 0)
    urls = cfg.data_source_urls or [cfg.data_source_url]
    urls = [u for u in urls if u]

    for url in urls:
        for attempt in range(1, retry_count + 1):
            try:
                temp_dir.mkdir(parents=True, exist_ok=True)
                if retry_count > 1:
                    print(f"[DATA] download attempt {attempt}/{retry_count}")
                print("[DATA] downloading latest data package...")
                print(f"   source: {url}")
                urlretrieve(url, download_path)
                size_mb = download_path.stat().st_size / (1024 * 1024)
                print(f"[OK] download completed: {size_mb:.1f} MB")
                if extract_dir.exists():
                    shutil.rmtree(extract_dir)
                extract_dir.mkdir(parents=True, exist_ok=True)
                with tarfile.open(download_path, "r:gz") as tar:
                    tar.extractall(extract_dir)
                qlib_bin_dir = extract_dir / "qlib_bin"
                if not qlib_bin_dir.exists():
                    print("[ERROR] extracted data missing qlib_bin directory")
                    return False
                for dirname in ("features", "instruments", "calendars"):
                    if not (qlib_bin_dir / dirname).exists():
                        print(f"[ERROR] missing required dir: {dirname}")
                        return False
                if target_path.exists():
                    shutil.rmtree(target_path)
                shutil.copytree(qlib_bin_dir, target_path)
                print("[OK] data update completed")
                return True
            except Exception as err:
                print(f"[ERROR] data update failed: {err}")
                if download_path.exists():
                    download_path.unlink()
                if extract_dir.exists():
                    shutil.rmtree(extract_dir)
                if attempt < retry_count:
                    print(f"[DATA] retrying in {retry_interval} seconds...")
                    time.sleep(retry_interval)
        if len(urls) > 1:
            print("[DATA] switching to next download source...")
    return False


def _ensure_data_ready(
    provider_uri: str,
    instruments,
    required_date: str,
    cfg: DataUpdateConfig,
) -> None:
    data_path = _provider_path_from_uri(provider_uri)
    missing = _missing_required_data(data_path, instruments)
    outdated, latest = _is_data_outdated(data_path, required_date, instruments)
    if not cfg.enable_auto_update:
        if missing:
            print(f"[WARN] missing data: {missing}")
        if outdated:
            print(f"[WARN] local latest date {latest} is older than {required_date}")
        return
    if not missing and not outdated:
        print(f"[DATA] local data is up to date (latest {latest})")
        return
    print("[DATA] auto update triggered...")
    if not _download_and_update_data(cfg, data_path):
        raise RuntimeError("data update failed")


def _load_trade_calendar(path: Path) -> List[str]:
    df = pd.read_csv(path, encoding="utf-8")
    if "date" not in df.columns:
        raise ValueError(f"calendar csv must contain 'date' column: {path}")
    dates = pd.to_datetime(df["date"]).dropna().dt.strftime("%Y-%m-%d")
    dates = sorted(set(dates))
    return dates


def _load_qlib_calendar_dates(provider_uri: str, freq: str) -> List[str]:
    data_path = _provider_path_from_uri(provider_uri)
    cal_dir = data_path / "calendars"
    if freq != "day":
        freq = "day"
    day_file = cal_dir / f"{freq}.txt"
    day_future_file = cal_dir / f"{freq}_future.txt"
    dates: List[str] = []
    for path in (day_file, day_future_file):
        if not path.exists():
            continue
        with path.open("r") as f:
            for line in f:
                value = line.strip()
                if value:
                    dates.append(value)
    dates = sorted(set(dates))
    return dates


def _previous_trade_date(trade_date: str, calendar_dates: List[str]) -> str:
    if trade_date not in calendar_dates:
        return trade_date
    idx = calendar_dates.index(trade_date)
    if idx <= 0:
        return trade_date
    return calendar_dates[idx - 1]


def _resolve_trade_date(value: str) -> str:
    if not value or str(value).lower() == "auto":
        return date.today().strftime("%Y-%m-%d")
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _resolve_pred_date(trade_date: str, freq: str) -> str:
    calendar = D.calendar(end_time=trade_date, freq=freq)
    if calendar is None or len(calendar) == 0:
        raise ValueError(f"no qlib calendar data on or before {trade_date}")
    return pd.Timestamp(calendar[-1]).strftime("%Y-%m-%d")


def _list_instruments_for_date(instruments, target_date: str, freq: str) -> List[str]:
    if isinstance(instruments, (list, tuple, set)):
        return [str(x) for x in instruments]
    if isinstance(instruments, dict):
        inst_config = instruments
    elif isinstance(instruments, str):
        inst_config = D.instruments(instruments)
    else:
        return []
    return D.list_instruments(inst_config, start_time=target_date, end_time=target_date, freq=freq, as_list=True)


def _resolve_pred_date_with_data(
    trade_date: str,
    freq: str,
    instruments,
    max_lookback: int,
) -> str:
    calendar = D.calendar(end_time=trade_date, freq=freq)
    if calendar is None or len(calendar) == 0:
        raise ValueError(f"no qlib calendar data on or before {trade_date}")
    max_lookback = max(int(max_lookback or 0), 0)
    for offset, cal_date in enumerate(reversed(calendar)):
        if offset > max_lookback:
            break
        date_str = pd.Timestamp(cal_date).strftime("%Y-%m-%d")
        inst_list = _list_instruments_for_date(instruments, date_str, freq)
        if not inst_list:
            continue
        try:
            data = D.features(inst_list, ["$close"], start_time=date_str, end_time=date_str, freq=freq)
        except Exception as err:
            print(f"[WARN] feature check failed for {date_str}: {err}")
            continue
        if data is not None and not data.empty:
            if offset > 0:
                print(f"[INFO] pred_date fallback to {date_str} (latest date has no data)")
            return date_str
    raise ValueError("no available feature data within lookback window")


def _load_model(recorder) -> Optional[object]:
    candidates = ["params.pkl", "model.pkl", "trained_model.pkl", "lgb_model.pkl"]
    for name in candidates:
        try:
            model = recorder.load_object(name)
            print(f"[OK] model loaded: {name}")
            return model
        except Exception as err:
            print(f"[WARN] cannot load {name}: {err}")
    print(f"[ERROR] no model found in recorder; tried {candidates}")
    return None


def _build_dataset(pred_cfg: PredictionConfig):
    windows = []
    default_window = pred_cfg.min_history_days
    if default_window is not None:
        windows.append(default_window)
    windows.extend([90, 60, 30])
    seen = set()
    for window in windows:
        if window in seen:
            continue
        seen.add(window)
        dataset_cfg = pred_cfg.dataset_config(history_days=window)
        try:
            dataset = init_instance_by_config(dataset_cfg)
        except MemoryError:
            print(f"[WARN] dataset window {window} out of memory, retry shorter window")
            continue
        if window != default_window:
            pred_cfg.min_history_days = window
        return dataset
    raise MemoryError("dataset init failed for all window sizes")


def _generate_predictions(model, dataset, recorder) -> pd.DataFrame:
    signal_record = SignalRecord(model=model, dataset=dataset, recorder=recorder)
    signal_record.generate()
    predictions = signal_record.load("pred.pkl")
    if isinstance(predictions, pd.Series):
        predictions = predictions.to_frame("score")
    return predictions


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
        weights = np.full(len(scores), 1.0 / len(scores))
    return pd.Series(weights, index=scores.index)


def _prepare_predictions(predictions: pd.DataFrame, pred_cfg: PredictionConfig) -> pd.DataFrame:
    if predictions is None or predictions.empty:
        return pd.DataFrame(columns=["instrument", "datetime", "score", "target_weight"])

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

    if pred_cfg.min_score_threshold > 0:
        df = df[df["score"] >= pred_cfg.min_score_threshold]

    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    return df


def _select_topk(pred_df: pd.DataFrame, top_k: int) -> pd.DataFrame:
    if top_k <= 0 or pred_df.empty:
        return pred_df
    return pred_df.sort_values("score", ascending=False).head(top_k).reset_index(drop=True)


def _build_pred_score(pred_df: pd.DataFrame, pred_date: str) -> pd.Series:
    if pred_df is None or pred_df.empty:
        return pd.Series(dtype=float)
    df = pred_df
    if "datetime" in df.columns and pred_date:
        df = df[df["datetime"] == pred_date]
    if df.empty:
        return pd.Series(dtype=float)
    df = df.copy()
    df["instrument"] = df["instrument"].astype(str)
    if df.duplicated(subset="instrument").any():
        df = df.drop_duplicates(subset="instrument", keep="last")
    return pd.Series(df["score"].values, index=df["instrument"])


def _generate_orders_topk_dropout(
    pred_score: pd.Series,
    position: Position,
    exchange: Exchange,
    trade_start: pd.Timestamp,
    trade_end: pd.Timestamp,
    risk_degree: float,
    top_k: int,
    n_drop: int,
    method_sell: str,
    method_buy: str,
    hold_thresh: int,
    trade_freq: str,
    only_tradable: bool,
    forbid_all_trade_at_limit: bool,
) -> Tuple[List[Order], List[str]]:
    if pred_score is None or pred_score.empty:
        return [], []

    top_k = max(int(top_k or 0), 0)
    n_drop = max(int(n_drop or 0), 0)
    if top_k <= 0:
        return [], []

    current_temp = copy.deepcopy(position)
    sell_order_list: List[Order] = []
    buy_order_list: List[Order] = []
    cash = current_temp.get_cash()
    current_stock_list = current_temp.get_stock_list()

    def _trade_dir(direction: OrderDir) -> Optional[OrderDir]:
        return None if forbid_all_trade_at_limit else direction

    if only_tradable:
        def get_first_n(li, n, reverse: bool = False):
            cur_n = 0
            res = []
            for si in reversed(li) if reverse else li:
                if exchange.is_stock_tradable(stock_id=si, start_time=trade_start, end_time=trade_end):
                    res.append(si)
                    cur_n += 1
                    if cur_n >= n:
                        break
            return res[::-1] if reverse else res

        def get_last_n(li, n):
            return get_first_n(li, n, reverse=True)

        def filter_stock(li):
            return [
                si
                for si in li
                if exchange.is_stock_tradable(stock_id=si, start_time=trade_start, end_time=trade_end)
            ]

    else:
        def get_first_n(li, n):
            return list(li)[:n]

        def get_last_n(li, n):
            return list(li)[-n:]

        def filter_stock(li):
            return li

    last = pred_score.reindex(current_stock_list).sort_values(ascending=False).index

    if method_buy == "top":
        today = get_first_n(
            pred_score[~pred_score.index.isin(last)].sort_values(ascending=False).index,
            n_drop + top_k - len(last),
        )
    elif method_buy == "random":
        topk_candi = get_first_n(pred_score.sort_values(ascending=False).index, top_k)
        candi = list(filter(lambda x: x not in last, topk_candi))
        n = n_drop + top_k - len(last)
        try:
            today = np.random.choice(candi, n, replace=False)
        except ValueError:
            today = candi
    else:
        raise ValueError(f"unsupported method_buy: {method_buy}")

    comb = pred_score.reindex(last.union(pd.Index(today))).sort_values(ascending=False).index

    if method_sell == "bottom":
        sell = last[last.isin(get_last_n(comb, n_drop))]
    elif method_sell == "random":
        candi = filter_stock(last)
        try:
            sell = pd.Index(np.random.choice(candi, n_drop, replace=False) if len(last) else [])
        except ValueError:
            sell = candi
    else:
        raise ValueError(f"unsupported method_sell: {method_sell}")

    buy = today[: len(sell) + top_k - len(last)]

    for code in current_stock_list:
        if not exchange.is_stock_tradable(
            stock_id=code,
            start_time=trade_start,
            end_time=trade_end,
            direction=_trade_dir(OrderDir.SELL),
        ):
            continue
        if code in sell:
            if hold_thresh > 0 and current_temp.get_stock_count(code, bar=trade_freq) < hold_thresh:
                continue
            sell_amount = current_temp.get_stock_amount(code=code)
            sell_order = Order(
                stock_id=code,
                amount=sell_amount,
                start_time=trade_start,
                end_time=trade_end,
                direction=OrderDir.SELL,
            )
            if exchange.check_order(sell_order):
                sell_order_list.append(sell_order)
                trade_val, trade_cost, _ = exchange.deal_order(sell_order, position=current_temp)
                cash += trade_val - trade_cost

    value = cash * risk_degree / len(buy) if len(buy) > 0 else 0.0

    for code in buy:
        if not exchange.is_stock_tradable(
            stock_id=code,
            start_time=trade_start,
            end_time=trade_end,
            direction=_trade_dir(OrderDir.BUY),
        ):
            continue
        buy_price = exchange.get_deal_price(
            stock_id=code, start_time=trade_start, end_time=trade_end, direction=OrderDir.BUY
        )
        if buy_price is None or pd.isna(buy_price) or buy_price <= 0:
            continue
        buy_amount = value / buy_price
        factor = exchange.get_factor(stock_id=code, start_time=trade_start, end_time=trade_end)
        buy_amount = exchange.round_amount_by_trade_unit(
            buy_amount, factor=factor, stock_id=code, start_time=trade_start, end_time=trade_end
        )
        if buy_amount <= 0:
            continue
        buy_order = Order(
            stock_id=code,
            amount=buy_amount,
            start_time=trade_start,
            end_time=trade_end,
            direction=OrderDir.BUY,
        )
        buy_order_list.append(buy_order)

    return sell_order_list + buy_order_list, list(buy)


def _fetch_prices(instruments: Sequence[str], pred_date: str, price_search_days: int, freq: str) -> pd.DataFrame:
    if not instruments:
        return pd.DataFrame(columns=["instrument", "price"])

    tried_dates = set()
    search_date = pd.Timestamp(pred_date)
    max_attempts = max(int(price_search_days or 0), 0)
    attempt = 0
    while attempt <= max_attempts:
        date_str = search_date.strftime("%Y-%m-%d")
        try:
            data = D.features(
                instruments,
                ["$close"],
                start_time=date_str,
                end_time=date_str,
                freq=freq,
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


def _read_positions(path: Path, total_cash_fallback: float) -> Tuple[Dict[str, float], float]:
    if not path.exists():
        raise FileNotFoundError(f"positions file not found: {path}")
    df = pd.read_csv(path, encoding="utf-8-sig").rename(columns=str.lower)
    if "code" not in df.columns:
        raise ValueError(f"positions csv must contain 'code' column, got {df.columns}")
    pos_col = "position" if "position" in df.columns else "pos"
    if pos_col not in df.columns:
        raise ValueError(f"positions csv must contain 'position' or 'pos', got {df.columns}")

    holdings: Dict[str, float] = {}
    cash: Optional[float] = None
    for _, row in df.iterrows():
        code = str(row["code"]).strip()
        if not code:
            continue
        amount = float(row[pos_col])
        if code.upper() == "CASH":
            cash = amount
            continue
        holdings[code] = amount

    if cash is None:
        cash = float(total_cash_fallback or 0.0)
        print(f"[WARN] CASH row not found, fallback to total_cash={cash:.2f}")

    return holdings, float(cash)


def _load_holdings_history(path: Path) -> Dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        print(f"[WARN] invalid holdings history: {path}, using empty history")
        return {}


def _save_holdings_history(path: Path, history: Dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_backtest_window(recorder) -> Optional[Tuple[str, str]]:
    for obj_name in ("portfolio_analysis/report_normal_1day.pkl", "report_normal_1day.pkl"):
        try:
            report = recorder.load_object(obj_name)
        except Exception:
            continue
        if isinstance(report, pd.DataFrame) and not report.empty:
            idx = pd.to_datetime(report.index)
            return idx.min().strftime("%Y-%m-%d"), idx.max().strftime("%Y-%m-%d")
    return None


def _load_workflow_position_counts(recorder, date_str: str, bar: str) -> Dict[str, float]:
    try:
        positions_dict = recorder.load_object("portfolio_analysis/positions_normal_1day.pkl")
    except Exception as err:
        print(f"[WARN] cannot load workflow positions: {err}")
        return {}
    if positions_dict is None:
        print("[WARN] workflow positions empty")
        return {}
    target_date = pd.Timestamp(date_str)
    position = positions_dict.get(target_date)
    if position is None:
        print(f"[WARN] workflow positions missing date: {date_str}")
        return {}
    counts: Dict[str, float] = {}
    for code in position.get_stock_list():
        try:
            counts[code] = float(position.get_stock_count(code, bar=bar))
        except Exception:
            continue
    if counts:
        print(f"[INFO] loaded workflow position counts: {len(counts)}")
    return counts


def _cleanup_holdings_history(history: Dict[str, dict], holdings: Dict[str, float]) -> Dict[str, dict]:
    removed = []
    for code in list(history.keys()):
        if code not in holdings:
            removed.append(code)
            del history[code]
    if removed:
        print(f"[INFO] cleaned history entries: {', '.join(removed[:8])}")
    return history


def _calendar_index(calendar_dates: List[str]) -> Dict[str, int]:
    return {d: i for i, d in enumerate(calendar_dates)}


def _calculate_hold_days(
    holdings: Dict[str, float],
    history: Dict[str, dict],
    trade_date: str,
    cal_index: Dict[str, int],
    hold_thresh: int,
) -> Dict[str, int]:
    hold_days: Dict[str, int] = {}
    trade_idx = cal_index.get(trade_date)
    for code in holdings:
        entry = history.get(code)
        if not entry or "buy_date" not in entry:
            hold_days[code] = hold_thresh + 100
            continue
        buy_date = entry["buy_date"]
        if trade_idx is not None and buy_date in cal_index:
            delta = trade_idx - cal_index[buy_date]
            hold_days[code] = max(int(delta), 0)
        else:
            try:
                delta = (pd.Timestamp(trade_date) - pd.Timestamp(buy_date)).days
                hold_days[code] = max(int(delta), 0)
            except Exception:
                hold_days[code] = hold_thresh + 100
    return hold_days


def _filter_sell_orders_by_hold(
    orders: List[object],
    hold_days: Dict[str, int],
    hold_thresh: int,
) -> List[object]:
    filtered = []
    blocked = []
    for order in orders:
        if order.direction == OrderDir.SELL:
            days = hold_days.get(order.stock_id, hold_thresh + 100)
            if days < hold_thresh:
                blocked.append(order.stock_id)
                continue
        filtered.append(order)
    if blocked:
        print(f"[INFO] blocked sells due to hold_thresh: {sorted(set(blocked))}")
    return filtered


def _ensure_position_prices(
    position: Position,
    exchange: Exchange,
    trade_start: pd.Timestamp,
    trade_end: pd.Timestamp,
    price_map: Dict[str, float],
) -> None:
    for code in position.get_stock_list():
        pos_info = position.position.get(code, {})
        price = np.nan
        if isinstance(pos_info, dict):
            price = pos_info.get("price", np.nan)
        if pd.notna(price) and float(price) > 0:
            continue
        try:
            price = exchange.get_close(code, start_time=trade_start, end_time=trade_end)
        except Exception:
            price = np.nan
        if (pd.isna(price) or float(price) <= 0) and code in price_map:
            price = price_map[code]
        if pd.notna(price) and float(price) > 0:
            position.update_stock_price(code, float(price))


def _resolve_price(
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


def _orders_to_frame(
    orders: List[object],
    exchange: Exchange,
    base_df: pd.DataFrame,
    trade_start: pd.Timestamp,
    trade_end: pd.Timestamp,
    min_shares: int,
    trade_date: str,
) -> pd.DataFrame:
    rows = []
    min_lot = max(int(min_shares or 1), 1)

    for order in orders:
        amount = getattr(order, "deal_amount", None)
        if amount is None or not np.isfinite(amount) or amount <= 0:
            amount = order.amount
        if amount is None or not np.isfinite(amount) or amount <= 0:
            continue
        factor = exchange.get_factor(order.stock_id, trade_start, trade_end)
        if factor is None or not np.isfinite(factor) or factor <= 0:
            factor = 1.0
        raw_shares = float(amount) * float(factor)
        raw_shares = math.floor(raw_shares / min_lot) * min_lot
        if raw_shares < min_lot:
            continue

        shares_adj = raw_shares / float(factor)
        price_adj = _resolve_price(exchange, order, trade_start, trade_end, base_df)
        amount_adj = float(shares_adj * price_adj) if pd.notna(price_adj) else 0.0

        price_raw = float(price_adj / factor) if pd.notna(price_adj) else np.nan
        amount_raw = float(raw_shares * price_raw) if pd.notna(price_raw) else 0.0

        direction = "买入" if order.direction == OrderDir.BUY else "卖出"
        stock_row = base_df[base_df["instrument"] == order.stock_id]
        score = float(stock_row["score"].iloc[0]) if not stock_row.empty else np.nan
        weight = float(stock_row["target_weight"].iloc[0]) if not stock_row.empty else 0.0

        rows.append(
            {
                "order_id": f"{order.stock_id}_{'BUY' if direction == '买入' else 'SELL'}_{trade_date.replace('-', '')}",
                "stock": order.stock_id,
                "action": direction,
                "shares": float(shares_adj),
                "price": float(price_adj) if pd.notna(price_adj) else np.nan,
                "amount": amount_adj,
                "score": score,
                "weight": weight,
                "price_raw": price_raw,
                "shares_raw": int(raw_shares),
                "amount_raw": amount_raw,
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "order_id",
                "stock",
                "action",
                "shares",
                "price",
                "amount",
                "score",
                "weight",
                "price_raw",
                "shares_raw",
                "amount_raw",
            ]
        )
    return pd.DataFrame(rows)


def _simulate_execution(
    orders: List[Order],
    position: Position,
    exchange: Exchange,
) -> Tuple[List[Order], Position]:
    executed: List[Order] = []
    for order in orders:
        exchange.deal_order(order, position=position)
        if getattr(order, "deal_amount", 0) > 0:
            executed.append(order)
    return executed, position


def _position_to_raw_holdings(
    position: Position,
    exchange: Exchange,
    trade_start: pd.Timestamp,
    trade_end: pd.Timestamp,
) -> Tuple[Dict[str, float], float]:
    holdings: Dict[str, float] = {}
    for code in position.get_stock_list():
        amount_adj = float(position.get_stock_amount(code))
        factor = exchange.get_factor(code, trade_start, trade_end)
        if factor is None or not np.isfinite(factor) or factor <= 0:
            factor = 1.0
        raw = int(round(amount_adj * factor))
        if raw != 0:
            holdings[code] = float(raw)
    cash = float(position.get_cash())
    return holdings, cash


def _apply_orders_to_positions(
    holdings: Dict[str, float],
    cash: float,
    orders_df: pd.DataFrame,
) -> Tuple[Dict[str, float], float]:
    next_holdings = dict(holdings)
    next_cash = float(cash)
    if orders_df is None or orders_df.empty:
        return next_holdings, next_cash
    for _, row in orders_df.iterrows():
        code = str(row.get("stock", "")).strip()
        if not code:
            continue
        shares_raw = float(row.get("shares_raw", 0.0) or 0.0)
        amount_raw = float(row.get("amount_raw", 0.0) or 0.0)
        if shares_raw <= 0:
            continue
        action = str(row.get("action", "")).strip()
        if action == "买入":
            next_holdings[code] = next_holdings.get(code, 0.0) + shares_raw
            next_cash -= amount_raw
        elif action == "卖出":
            next_holdings[code] = next_holdings.get(code, 0.0) - shares_raw
            next_cash += amount_raw
    cleaned = {}
    for code, amount in next_holdings.items():
        if amount >= 1:
            cleaned[code] = float(amount)
    return cleaned, next_cash


def _write_positions_csv(path: Path, holdings: Dict[str, float], cash: float, encoding: str) -> None:
    rows = [{"code": "CASH", "position": float(cash)}]
    for code in sorted(holdings.keys()):
        amount = holdings[code]
        if float(amount).is_integer():
            amount = int(amount)
        rows.append({"code": code, "position": amount})
    df = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding=encoding)


def _update_history_after_buy(history: Dict[str, dict], orders_df: pd.DataFrame, trade_date: str) -> None:
    if orders_df.empty:
        return
    buys = orders_df[orders_df["action"] == "买入"]
    for _, row in buys.iterrows():
        code = str(row["stock"])
        shares_raw = int(row.get("shares_raw", 0))
        if code not in history:
            history[code] = {"buy_date": trade_date, "amount": shares_raw}
        else:
            history[code]["amount"] = shares_raw


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manual daily trading for Qlib.")
    parser.add_argument("--trade-date", default=None, help="Trade date, e.g. 2025-01-06; default is today.")
    return parser.parse_args()


def main(trade_date_override: Optional[str] = None) -> int:
    cfg = DEFAULT_CONFIG

    trade_date_value = trade_date_override or cfg.get("runtime", {}).get("trade_date", "auto")
    trade_date = _resolve_trade_date(trade_date_value)
    qlib_calendar_provider = cfg.get("qlib_init", {}).get("provider_uri", "~/.qlib/qlib_data/cn_data")
    calendar_dates = _load_qlib_calendar_dates(qlib_calendar_provider, "day")
    calendar_source = "qlib"
    if not calendar_dates:
        calendar_path = _resolve_path(cfg.get("calendar", {}).get("path", ""))
        if not calendar_path.exists():
            print(f"[ERROR] calendar csv not found: {calendar_path}")
            return 1
        calendar_dates = _load_trade_calendar(calendar_path)
        calendar_source = "local"
    print(f"[INFO] calendar_source: {calendar_source}")
    if trade_date not in calendar_dates:
        print(f"[WARN] {trade_date} is not a trading day in calendar, exit.")
        return 0
    required_pred_date = _previous_trade_date(trade_date, calendar_dates)

    pred_raw = dict(cfg.get("prediction", {}) or {})
    align_cfg = cfg.get("workflow_alignment", {}) or {}
    align_enabled = bool(align_cfg.get("enabled", False))
    mode = str(align_cfg.get("mode", "align")).lower()
    if mode not in ("align", "live"):
        print(f"[WARN] workflow_alignment.mode={mode} not recognized, fallback to align")
        mode = "align"
    use_required_pred_date = bool(align_cfg.get("use_required_pred_date", False)) if align_enabled else False
    use_position_count = bool(align_cfg.get("use_position_count", False)) if align_enabled else False
    use_recorder_pred = bool(align_cfg.get("use_recorder_pred", False)) if align_enabled else False
    use_backtest_window = bool(align_cfg.get("use_backtest_window", False)) if align_enabled else False
    use_execution_simulator = bool(align_cfg.get("use_execution_simulator", False)) if align_enabled else False
    if align_enabled and mode == "live":
        use_position_count = False
        use_recorder_pred = False
        use_backtest_window = False
    if align_enabled:
        market = align_cfg.get("market")
        if market:
            pred_raw["instruments"] = market
        handler_cfg = align_cfg.get("handler", {}) or {}
        if handler_cfg:
            handler_kwargs = dict(pred_raw.get("handler_kwargs", {}) or {})
            for key in ("start_time", "fit_start_time", "fit_end_time"):
                value = handler_cfg.get(key)
                if value:
                    handler_kwargs[key] = value
            end_value = handler_cfg.get("end_time")
            if end_value:
                end_text = str(end_value).lower()
                if end_text not in ("pred_date", "auto"):
                    handler_kwargs["end_time"] = end_value
            pred_raw["handler_kwargs"] = handler_kwargs
        print(
            "[INFO] workflow_alignment enabled: "
            f"mode={mode}, "
            f"market={pred_raw.get('instruments')}, "
            f"handler_start_time={handler_cfg.get('start_time')}, "
            f"fit_start_time={handler_cfg.get('fit_start_time')}, "
            f"fit_end_time={handler_cfg.get('fit_end_time')}, "
            f"use_required_pred_date={use_required_pred_date}, "
            f"use_recorder_pred={use_recorder_pred}, "
            f"use_position_count={use_position_count}, "
            f"use_backtest_window={use_backtest_window}, "
            f"use_execution_simulator={use_execution_simulator}"
        )
    provider_uri = pred_raw.get("provider_uri", "~/.qlib/qlib_data/cn_data")
    instruments = pred_raw.get("instruments", "csi300")

    data_update_cfg = DataUpdateConfig(**cfg.get("data_update", {}))
    try:
        _ensure_data_ready(provider_uri, instruments, required_pred_date, data_update_cfg)
    except RuntimeError as err:
        print(f"[ERROR] {err}")
        return 1

    qlib_cfg = cfg.get("qlib_init", {})
    qlib.init(
        provider_uri=qlib_cfg.get("provider_uri", "~/.qlib/qlib_data/cn_data"),
        region=qlib_cfg.get("region", "cn"),
        kernels=qlib_cfg.get("kernels", 1),
        joblib_backend=qlib_cfg.get("joblib_backend", "threading"),
        maxtasksperchild=qlib_cfg.get("maxtasksperchild", 1),
    )

    trading_cfg = TradingConfig(**cfg.get("trading", {}))
    pred_date_search_days = pred_raw.get("pred_date_search_days", 0)
    qlib_latest_date = _resolve_pred_date(trade_date, trading_cfg.trade_freq)
    print(f"[INFO] qlib_latest_date: {qlib_latest_date}")
    if use_required_pred_date and required_pred_date:
        try:
            pred_date = _resolve_pred_date_with_data(
                required_pred_date,
                trading_cfg.trade_freq,
                instruments,
                0,
            )
        except ValueError as err:
            print(f"[WARN] required_pred_date {required_pred_date} has no data: {err}")
            return 0
        print(f"[INFO] pred_date aligned to required_pred_date: {pred_date}")
    else:
        pred_date = _resolve_pred_date_with_data(
            trade_date,
            trading_cfg.trade_freq,
            instruments,
            pred_date_search_days,
        )
    if pred_date != trade_date:
        print(f"[INFO] trade_date={trade_date}, pred_date={pred_date}")
    print(f"[INFO] required_pred_date (calendar): {required_pred_date}")

    pred_kwargs = dict(pred_raw)
    pred_kwargs.pop("prediction_date", None)
    pred_cfg = PredictionConfig(**pred_kwargs, prediction_date=pred_date)
    mlruns_uri = _resolve_mlruns_uri(cfg.get("paths", {}).get("mlruns_uri", "auto"))
    R.set_uri(mlruns_uri)
    recorder = R.get_recorder(
        experiment_id=pred_cfg.experiment_id,
        recorder_id=pred_cfg.recorder_id,
    )
    model = _load_model(recorder)
    if model is None:
        return 1

    predictions = None
    if use_recorder_pred:
        try:
            predictions = recorder.load_object("pred.pkl")
            print("[INFO] loaded pred.pkl from recorder")
        except Exception as err:
            print(f"[WARN] cannot load recorder pred.pkl: {err}")
            predictions = None
    if predictions is None:
        dataset = _build_dataset(pred_cfg)
        predictions = _generate_predictions(model, dataset, recorder)
    pred_df = _prepare_predictions(predictions, pred_cfg)
    pred_score = _build_pred_score(pred_df, pred_date)
    if pred_score.empty:
        print("[WARN] empty predictions, exit")
        return 0
    if "datetime" in pred_df.columns:
        pred_df = pred_df[pred_df["datetime"] == pred_date].copy()
    else:
        pred_df = pred_df.copy()
    if pred_df.empty:
        print("[WARN] empty predictions for pred_date, exit")
        return 0
    pred_df = pred_df.drop_duplicates(subset="instrument", keep="last").reset_index(drop=True)
    pred_df["target_weight"] = 0.0

    positions_path = _resolve_path(cfg.get("paths", {}).get("positions", ""))
    holdings_raw, cash = _read_positions(positions_path, trading_cfg.total_cash)
    position_counts: Dict[str, float] = {}
    if use_position_count:
        position_counts = _load_workflow_position_counts(recorder, pred_date, trading_cfg.trade_freq)
        if not position_counts:
            print("[WARN] workflow position counts unavailable, fallback to history-based hold filter")
            use_position_count = False

    strategy_cfg = cfg.get("strategy", {}) or {}
    if not strategy_cfg.get("enabled", True):
        print("[WARN] strategy disabled, exit")
        return 0
    top_k = int(strategy_cfg.get("top_k", pred_cfg.top_k or 0))
    n_drop = int(strategy_cfg.get("n_drop", 0))
    method_buy = strategy_cfg.get("method_buy", "top")
    method_sell = strategy_cfg.get("method_sell", "bottom")
    hold_thresh = int(strategy_cfg.get("hold_thresh", trading_cfg.hold_thresh or 0))
    if hold_thresh > 0:
        trading_cfg.hold_thresh = hold_thresh
    only_tradable = strategy_cfg.get("only_tradable")
    if only_tradable is None:
        only_tradable = trading_cfg.only_tradable
    forbid_all_trade_at_limit = bool(strategy_cfg.get("forbid_all_trade_at_limit", True))

    codes = sorted(set(pred_df["instrument"].tolist()) | set(holdings_raw.keys()))
    trade_base_date = trade_date if use_required_pred_date else pred_date
    trade_start = pd.Timestamp(trade_base_date)
    trade_end = epsilon_change(trade_start + pd.Timedelta(days=1))
    start_date = (trade_start - pd.Timedelta(days=max(trading_cfg.price_search_days, 1))).strftime("%Y-%m-%d")
    end_date = trade_end.strftime("%Y-%m-%d")
    backtest_window = None
    if use_backtest_window:
        backtest_window = _load_backtest_window(recorder)
    if backtest_window:
        start_date, end_date = backtest_window
        print(f"[INFO] backtest_window from recorder: {start_date} -> {end_date}")

    exchange = Exchange(
        codes=codes,
        start_time=start_date,
        end_time=end_date,
        deal_price=trading_cfg.deal_price,
        freq=trading_cfg.trade_freq,
        open_cost=trading_cfg.open_cost,
        close_cost=trading_cfg.close_cost,
        min_cost=trading_cfg.min_cost,
        impact_cost=trading_cfg.impact_cost,
        limit_threshold=trading_cfg.limit_threshold,
    )

    price_df = _fetch_prices(codes, trade_base_date, trading_cfg.price_search_days, trading_cfg.trade_freq)
    price_map = dict(zip(price_df["instrument"], price_df["price"]))

    holdings_adj: Dict[str, float] = {}
    for code, raw_amount in holdings_raw.items():
        factor = exchange.get_factor(code, trade_start, trade_end)
        if factor is None or not np.isfinite(factor) or factor <= 0:
            factor = 1.0
            print(f"[WARN] missing factor for {code}, use 1.0")
        holdings_adj[code] = float(raw_amount) / float(factor)

    position_dict = {code: {"amount": amount} for code, amount in holdings_adj.items()}
    position = Position(cash=cash, position_dict=position_dict)
    if position_counts:
        for code in holdings_adj:
            count = position_counts.get(code, 0.0)
            position.update_stock_count(code, trading_cfg.trade_freq, count)
    if holdings_adj:
        try:
            position.fill_stock_value(start_time=trade_base_date, freq=trading_cfg.trade_freq)
        except Exception as err:
            print(f"[WARN] fill_stock_value failed: {err}")
        _ensure_position_prices(position, exchange, trade_start, trade_end, price_map)

    orders, buy_list = _generate_orders_topk_dropout(
        pred_score=pred_score,
        position=position,
        exchange=exchange,
        trade_start=trade_start,
        trade_end=trade_end,
        risk_degree=trading_cfg.risk_degree,
        top_k=top_k,
        n_drop=n_drop,
        method_sell=method_sell,
        method_buy=method_buy,
        hold_thresh=trading_cfg.hold_thresh,
        trade_freq=trading_cfg.trade_freq,
        only_tradable=bool(only_tradable),
        forbid_all_trade_at_limit=forbid_all_trade_at_limit,
    )

    history = None
    history_path = _resolve_path(cfg.get("paths", {}).get("holdings_history", ""))
    if not use_position_count:
        history = _load_holdings_history(history_path)
        history = _cleanup_holdings_history(history, holdings_raw)
        hold_days = _calculate_hold_days(
            holdings=holdings_raw,
            history=history,
            trade_date=trade_date,
            cal_index=_calendar_index(calendar_dates),
            hold_thresh=trading_cfg.hold_thresh,
        )
        orders = _filter_sell_orders_by_hold(orders, hold_days, trading_cfg.hold_thresh)
    if buy_list:
        target_weight = 1.0 / len(buy_list)
        pred_df.loc[pred_df["instrument"].isin(buy_list), "target_weight"] = target_weight

    executed_position = None
    if use_execution_simulator:
        exec_position = copy.deepcopy(position)
        orders, executed_position = _simulate_execution(orders, exec_position, exchange)

    orders_df = _orders_to_frame(
        orders=orders,
        exchange=exchange,
        base_df=pred_df,
        trade_start=trade_start,
        trade_end=trade_end,
        min_shares=trading_cfg.min_shares,
        trade_date=trade_date,
    )

    total_buy = float(orders_df.loc[orders_df["action"] == "买入", "amount_raw"].sum()) if not orders_df.empty else 0.0
    total_sell = float(orders_df.loc[orders_df["action"] == "卖出", "amount_raw"].sum()) if not orders_df.empty else 0.0

    print("[SUMMARY]")
    print(f"  trade_date: {trade_date}")
    print(f"  pred_date: {pred_date}")
    print(f"  buy_orders: {len(orders_df[orders_df['action'] == '买入'])}")
    print(f"  sell_orders: {len(orders_df[orders_df['action'] == '卖出'])}")
    print(f"  buy_amount_raw: {total_buy:,.2f}")
    print(f"  sell_amount_raw: {total_sell:,.2f}")

    orders_out = _resolve_path(cfg.get("paths", {}).get("orders_out", ""))
    orders_out.parent.mkdir(parents=True, exist_ok=True)
    encoding = cfg.get("output", {}).get("encoding", "utf-8-sig")
    orders_out.write_text(orders_df.to_csv(index=False), encoding=encoding)
    print(f"[OK] orders saved: {orders_out}")

    if not orders_df.empty:
        print("[ORDERS]")
        for action in ("买入", "卖出"):
            subset = orders_df[orders_df["action"] == action].copy()
            if subset.empty:
                continue
            subset = subset.sort_values(["amount_raw"], ascending=False)
            print(f"  {action}({len(subset)}):")
            for _, row in subset.iterrows():
                print(
                    f"    {row['stock']} shares_raw={int(row['shares_raw'])} "
                    f"price_raw={row['price_raw']:.4f} amount_raw={row['amount_raw']:.2f}"
                )

    positions_next_path = _resolve_path(cfg.get("paths", {}).get("positions_next", ""))
    if use_execution_simulator and executed_position is not None:
        next_holdings, next_cash = _position_to_raw_holdings(executed_position, exchange, trade_start, trade_end)
    else:
        next_holdings, next_cash = _apply_orders_to_positions(holdings_raw, cash, orders_df)
    _write_positions_csv(positions_next_path, next_holdings, next_cash, encoding)
    print(f"[OK] next positions saved: {positions_next_path}")

    if history is not None:
        _update_history_after_buy(history, orders_df, trade_date)
        _save_holdings_history(history_path, history)

    return 0


if __name__ == "__main__":
    args = _parse_args()
    raise SystemExit(main(trade_date_override=args.trade_date))

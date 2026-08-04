"""影子回放与 Qlib 普通回测的同口径对账工具。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from . import config as C
from . import data, gate, ledger, nightly, signal_


@dataclass(frozen=True)
class OrderSnapshot:
    code: str
    side: str
    shares: int


@dataclass
class DailySnapshot:
    date: str
    nav: float
    cash: float
    gate_on: bool
    holdings: Dict[str, int]
    orders: List[OrderSnapshot]
    receipts: Dict[Tuple[str, str], str]


@dataclass
class ParityResult:
    daily_compare: pd.DataFrame
    holdings_compare: pd.DataFrame
    orders_compare: pd.DataFrame
    summary: dict


class MarketCache:
    """一次加载历史行情，再按交易日提供与 data.day_bars 相同的切片。"""

    REQUIRED_COLUMNS = ["open", "close", "volume", "factor", "prev_close"]

    def __init__(self, frame: pd.DataFrame):
        if frame.empty:
            raise ValueError("行情缓存为空")
        missing = [column for column in self.REQUIRED_COLUMNS if column not in frame.columns]
        if missing:
            raise ValueError(f"行情缓存缺少字段: {missing}")
        if not isinstance(frame.index, pd.MultiIndex):
            raise ValueError("行情缓存必须使用 (datetime, instrument) MultiIndex")
        if set(frame.index.names) != {"datetime", "instrument"}:
            raise ValueError(f"行情缓存索引名错误: {frame.index.names}")
        self.frame = frame.reorder_levels(["datetime", "instrument"]).sort_index()

    @classmethod
    def from_qlib(cls, start: str, end: str) -> "MarketCache":
        from qlib.data import D

        data.init_qlib()
        expressions = ["$open", "$close", "$volume", "$factor", "Ref($close,1)"]
        frame = D.features(
            D.instruments(C.POOL),
            expressions,
            start_time=start,
            end_time=end,
        )
        frame.columns = cls.REQUIRED_COLUMNS
        return cls(frame)

    def day_bars(
        self,
        date: str,
        fields=("$open", "$close", "$volume", "$factor"),
    ) -> pd.DataFrame:
        timestamp = pd.Timestamp(date)
        dates = self.frame.index.get_level_values("datetime")
        if timestamp not in dates:
            raise ValueError(f"行情缓存缺少日期: {date}")
        requested = [field.replace("$", "") for field in fields]
        missing = [field for field in requested if field not in self.frame.columns]
        if missing:
            raise ValueError(f"行情缓存缺少请求字段: {missing}")
        columns = requested + (["prev_close"] if "prev_close" not in requested else [])
        return self.frame.xs(timestamp, level="datetime")[columns].copy()

    @property
    def latest_date(self) -> str:
        return self.frame.index.get_level_values("datetime").max().strftime("%Y-%m-%d")


def validate_snapshot_dates(snapshots: Dict[str, DailySnapshot], expected_dates: List[str]) -> None:
    missing = [date for date in expected_dates if date not in snapshots]
    if missing:
        preview = ", ".join(missing[:5])
        raise ValueError(f"快照缺少日期: {preview}")


def _holding_rows(qlib: DailySnapshot, shadow: DailySnapshot) -> List[dict]:
    rows = []
    for code in sorted(set(qlib.holdings) | set(shadow.holdings)):
        qlib_shares = int(qlib.holdings.get(code, 0))
        shadow_shares = int(shadow.holdings.get(code, 0))
        rows.append(
            {
                "date": qlib.date,
                "code": code,
                "qlib_shares": qlib_shares,
                "shadow_shares": shadow_shares,
                "shares_delta": shadow_shares - qlib_shares,
                "match": qlib_shares == shadow_shares,
            }
        )
    return rows


def _aggregate_orders(orders: List[OrderSnapshot]) -> Dict[Tuple[str, str], int]:
    result: Dict[Tuple[str, str], int] = {}
    for order in orders:
        key = (order.code, order.side)
        result[key] = result.get(key, 0) + int(order.shares)
    return result


def _order_rows(qlib: DailySnapshot, shadow: DailySnapshot) -> List[dict]:
    qlib_orders = _aggregate_orders(qlib.orders)
    shadow_orders = _aggregate_orders(shadow.orders)
    rows = []
    for code, side in sorted(set(qlib_orders) | set(shadow_orders)):
        qlib_shares = qlib_orders.get((code, side), 0)
        shadow_shares = shadow_orders.get((code, side), 0)
        rows.append(
            {
                "date": qlib.date,
                "code": code,
                "side": side,
                "qlib_shares": qlib_shares,
                "shadow_shares": shadow_shares,
                "shares_delta": shadow_shares - qlib_shares,
                "match": qlib_shares == shadow_shares,
                "shadow_status": shadow.receipts.get((code, side), ""),
            }
        )
    return rows


def _match_rate(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 1.0
    return float(frame["match"].mean())


def compare_snapshots(
    qlib: Dict[str, DailySnapshot],
    shadow: Dict[str, DailySnapshot],
    cash_tolerance: float = 0.01,
) -> ParityResult:
    qlib_dates = set(qlib)
    shadow_dates = set(shadow)
    if qlib_dates != shadow_dates:
        raise ValueError(
            f"快照日期集合不一致: qlib_only={sorted(qlib_dates - shadow_dates)[:5]}, "
            f"shadow_only={sorted(shadow_dates - qlib_dates)[:5]}"
        )

    daily_rows = []
    holding_rows = []
    order_rows = []
    for date in sorted(qlib_dates):
        qlib_snap = qlib[date]
        shadow_snap = shadow[date]
        if bool(qlib_snap.gate_on) != bool(shadow_snap.gate_on):
            raise ValueError(
                f"{date} 门控不一致: qlib={qlib_snap.gate_on}, shadow={shadow_snap.gate_on}"
            )
        nav_delta = float(shadow_snap.nav) - float(qlib_snap.nav)
        cash_delta = float(shadow_snap.cash) - float(qlib_snap.cash)
        daily_rows.append(
            {
                "date": date,
                "qlib_nav": float(qlib_snap.nav),
                "shadow_nav": float(shadow_snap.nav),
                "nav_delta": nav_delta,
                "nav_match": abs(nav_delta) <= cash_tolerance,
                "qlib_cash": float(qlib_snap.cash),
                "shadow_cash": float(shadow_snap.cash),
                "cash_delta": cash_delta,
                "cash_match": abs(cash_delta) <= cash_tolerance,
                "qlib_n_holdings": len(qlib_snap.holdings),
                "shadow_n_holdings": len(shadow_snap.holdings),
                "gate_on": bool(qlib_snap.gate_on),
            }
        )
        holding_rows.extend(_holding_rows(qlib_snap, shadow_snap))
        order_rows.extend(_order_rows(qlib_snap, shadow_snap))

    daily = pd.DataFrame(daily_rows)
    holdings = pd.DataFrame(
        holding_rows,
        columns=["date", "code", "qlib_shares", "shadow_shares", "shares_delta", "match"],
    )
    orders = pd.DataFrame(
        order_rows,
        columns=[
            "date",
            "code",
            "side",
            "qlib_shares",
            "shadow_shares",
            "shares_delta",
            "match",
            "shadow_status",
        ],
    )
    summary = {
        "daily_rows": len(daily),
        "qlib_final_nav": float(daily.iloc[-1]["qlib_nav"]) if not daily.empty else None,
        "shadow_final_nav": float(daily.iloc[-1]["shadow_nav"]) if not daily.empty else None,
        "max_abs_nav_delta": float(daily["nav_delta"].abs().max()) if not daily.empty else 0.0,
        "cash_match_rate": float(daily["cash_match"].mean()) if not daily.empty else 1.0,
        "nav_match_rate": float(daily["nav_match"].mean()) if not daily.empty else 1.0,
        "holding_match_rate": _match_rate(holdings),
        "order_match_rate": _match_rate(orders),
    }
    return ParityResult(daily, holdings, orders, summary)


def _scores_on(pred: pd.Series, date: str) -> pd.Series:
    try:
        scores = pred.xs(pd.Timestamp(date), level="datetime")
    except KeyError as exc:
        raise ValueError(f"预测缺少日期: {date}") from exc
    if isinstance(scores, pd.DataFrame):
        scores = scores.iloc[:, 0]
    scores = scores.copy()
    scores.name = "score"
    return scores


def _receipt_statuses(state_dir: Path, exec_date: str) -> Dict[Tuple[str, str], str]:
    path = state_dir / "receipts" / f"{exec_date}.csv"
    if not path.exists():
        return {}
    frame = pd.read_csv(path)
    if frame.empty:
        return {}
    return {
        (str(row.code), str(row.side)): str(row.status)
        for row in frame.itertuples(index=False)
    }


def run_shadow_replay(
    pred: pd.Series,
    gate_by_exec_date: pd.Series,
    cache: MarketCache,
    warmup: str,
    start: str,
    end: str,
    state_dir: Path,
    log=print,
) -> Dict[str, DailySnapshot]:
    """用归档预测和缓存行情驱动现有 nightly，返回正式比较区间快照。"""
    state_dir = Path(state_dir)
    if state_dir.exists() and any(state_dir.iterdir()):
        raise ValueError(f"影子回放目录必须为空: {state_dir}")
    state_dir.mkdir(parents=True, exist_ok=True)

    gate_series = gate_by_exec_date.copy()
    gate_series.index = pd.to_datetime(gate_series.index)
    calendar_days = [day for day in data.calendar() if warmup <= day <= end]
    if not calendar_days or calendar_days[0] != warmup:
        raise ValueError(f"预热日期不是有效交易日: {warmup}")

    original_state_dir = C.STATE_DIR
    original_day_bars = data.day_bars
    original_latest_data_date = data.latest_data_date
    original_scores_for = signal_.scores_for
    original_gate_for_next_day = gate.gate_for_next_day

    def cached_scores(date: str, log=print) -> pd.Series:
        scores = _scores_on(pred, date)
        log(f"[parity] {date} 使用归档预测 {len(scores)} 只")
        return scores

    def cached_gate(asof: str):
        exec_date = data.next_trade_date(asof)
        if exec_date is None or pd.Timestamp(exec_date) not in gate_series.index:
            raise ValueError(f"门控缺少执行日: {exec_date}")
        value = bool(gate_series.loc[pd.Timestamp(exec_date)])
        return value, f"parity cached gate {exec_date}={'on' if value else 'off'}"

    snapshots: Dict[str, DailySnapshot] = {}
    try:
        C.STATE_DIR = state_dir
        data.day_bars = cache.day_bars
        data.latest_data_date = lambda: cache.latest_date
        signal_.scores_for = cached_scores
        gate.gate_for_next_day = cached_gate

        for date in calendar_days:
            summary = nightly.run_evening(asof=date, skip_update=True, log=log)
            if date < start:
                continue
            state = ledger.load_state()
            orders = ledger.load_orders(date)
            snapshots[date] = DailySnapshot(
                date=date,
                nav=float(summary["nav"]),
                cash=float(state["cash"]),
                gate_on=bool(gate_series.loc[pd.Timestamp(date)]),
                holdings={code: int(shares) for code, shares in state["holdings"].items()},
                orders=[OrderSnapshot(order.code, order.side, int(order.shares)) for order in orders],
                receipts=_receipt_statuses(state_dir, date),
            )
    finally:
        C.STATE_DIR = original_state_dir
        data.day_bars = original_day_bars
        data.latest_data_date = original_latest_data_date
        signal_.scores_for = original_scores_for
        gate.gate_for_next_day = original_gate_for_next_day
    return snapshots

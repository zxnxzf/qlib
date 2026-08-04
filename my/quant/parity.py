"""影子回放与 Qlib 普通回测的同口径对账工具。"""

import json
from collections import Counter
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


def position_to_holdings(position, factors: pd.Series = None) -> Dict[str, int]:
    """将 Qlib Position 转为仅含股票的持仓快照。"""
    holdings = {}
    for code in position.get_stock_list():
        factor = 1.0
        if factors is not None:
            if code not in factors.index or pd.isna(factors.loc[code]):
                raise ValueError(f"Qlib 持仓缺少复权因子: {code}")
            factor = float(factors.loc[code])
        shares = int(round(float(position.get_stock_amount(code)) * factor))
        if shares:
            holdings[str(code)] = shares
    return holdings


def normalize_qlib_order(order, factor: float = None) -> OrderSnapshot:
    """将 Qlib Order 统一为对账用订单格式。"""
    from qlib.backtest.decision import OrderDir

    side = "buy" if order.direction == OrderDir.BUY else "sell"
    factor = factor if factor is not None else getattr(order, "factor", None)
    factor = 1.0 if factor is None else float(factor)
    return OrderSnapshot(
        str(order.stock_id), side, int(round(float(order.amount) * factor))
    )


def qlib_outputs_to_snapshots(
    report: pd.DataFrame,
    positions: dict,
    orders: dict,
    gate_by_exec_date: pd.Series,
    factor_cache=None,
) -> Dict[str, DailySnapshot]:
    """将 Qlib 回测原始输出归一为逐日快照。"""
    normalized_positions = {pd.Timestamp(key).normalize(): value for key, value in positions.items()}
    normalized_orders = {pd.Timestamp(key).normalize(): value for key, value in orders.items()}
    normalized_gate = gate_by_exec_date.copy()
    normalized_gate.index = pd.to_datetime(normalized_gate.index).normalize()

    snapshots = {}
    for raw_date, row in report.iterrows():
        timestamp = pd.Timestamp(raw_date).normalize()
        date = timestamp.strftime("%Y-%m-%d")
        if timestamp not in normalized_positions:
            raise ValueError(f"Qlib 持仓缺少日期: {date}")
        if timestamp not in normalized_gate.index:
            raise ValueError(f"Qlib 门控缺少执行日: {date}")
        snapshots[date] = DailySnapshot(
            date=date,
            nav=float(row["account"]),
            cash=float(row["cash"]),
            gate_on=bool(normalized_gate.loc[timestamp]),
            holdings=position_to_holdings(
                normalized_positions[timestamp],
                None
                if factor_cache is None
                else factor_cache.factors_on(date),
            ),
            orders=list(normalized_orders.get(timestamp, [])),
            receipts={},
        )
    return snapshots


def run_qlib_backtest(
    pred: pd.Series,
    gate_by_exec_date: pd.Series,
    start: str,
    end: str,
    factor_cache=None,
) -> Dict[str, DailySnapshot]:
    """按锁定的 10 万门控参数运行 Qlib 引擎回测并录制订单。"""
    from qlib.contrib.evaluate import backtest_daily

    from my.scripts.exp_gated_100k import GatedTopkDropout

    class RecordingGatedTopkDropout(GatedTopkDropout):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.recorded_orders = {}

        def generate_trade_decision(self, execute_result=None):
            trade_step = self.trade_calendar.get_trade_step()
            trade_start, _ = self.trade_calendar.get_step_time(trade_step)
            decision = super().generate_trade_decision(execute_result)
            self.recorded_orders[pd.Timestamp(trade_start).normalize()] = []
            for order in decision.get_decision():
                factor = self.trade_exchange.get_factor(
                    order.stock_id, order.start_time, order.end_time
                )
                self.recorded_orders[pd.Timestamp(trade_start).normalize()].append(
                    normalize_qlib_order(order, factor=factor)
                )
            return decision

    strategy = RecordingGatedTopkDropout(
        gate=gate_by_exec_date,
        signal=pred,
        topk=C.TOPK,
        n_drop=C.N_DROP,
        hold_thresh=1,
        only_tradable=True,
    )
    report, positions = backtest_daily(
        start_time=start,
        end_time=end,
        strategy=strategy,
        account=C.SHADOW_INIT_CASH,
        benchmark=C.BENCH,
        exchange_kwargs={
            "deal_price": "open",
            "limit_threshold": (
                f"$open/Ref($close,1)-1 > {C.LIMIT_TH}",
                f"$open/Ref($close,1)-1 < {-C.LIMIT_TH}",
            ),
            "open_cost": C.OPEN_COST,
            "close_cost": C.CLOSE_COST,
            "min_cost": C.MIN_COST,
            "impact_cost": C.IMPACT_COST,
        },
    )
    return qlib_outputs_to_snapshots(
        report,
        positions,
        strategy.recorded_orders,
        gate_by_exec_date,
        factor_cache=factor_cache,
    )


def write_parity_artifacts(result: ParityResult, output_dir: Path, metadata: dict) -> None:
    """写出可机读明细、摘要和人读报告。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result.daily_compare.to_csv(output_dir / "daily_compare.csv", index=False)
    result.holdings_compare.to_csv(output_dir / "holdings_compare.csv", index=False)
    result.orders_compare.to_csv(output_dir / "orders_compare.csv", index=False)

    payload = {**metadata, **result.summary}
    (output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    statuses = result.summary["shadow_receipt_status_counts"]
    status_text = "、".join(f"{key}={value}" for key, value in statuses.items()) or "无回执"
    report = f"""# 影子回放 vs Qlib 普通回测

区间：{metadata.get('start', '')} 至 {metadata.get('end', '')}，共 {result.summary['daily_rows']} 个交易日。

## 绩效

| 指标 | Qlib | 影子回放 |
|---|---:|---:|
| 期末资产 | {result.summary['qlib_final_nav']:,.2f} | {result.summary['shadow_final_nav']:,.2f} |
| 区间收益 | {result.summary['qlib_total_return']:.2%} | {result.summary['shadow_total_return']:.2%} |
| 年化收益 | {result.summary['qlib_annualized_return']:.2%} | {result.summary['shadow_annualized_return']:.2%} |
| 最大回撤 | {result.summary['qlib_max_drawdown']:.2%} | {result.summary['shadow_max_drawdown']:.2%} |

最大绝对资产差为 {result.summary['max_abs_nav_delta']:,.2f}。

## 对齐度

- 门控：在场 {result.summary['gate_on_days']} 日，离场 {result.summary['gate_off_days']} 日，两边逐日完全一致。
- 现金逐日匹配率：{result.summary['cash_match_rate']:.2%}。
- 持仓明细行匹配率：{result.summary['holding_match_rate']:.2%}；整日持仓完全一致率：{result.summary['holding_exact_day_match_rate']:.2%}。
- 订单明细行匹配率：{result.summary['order_match_rate']:.2%}；整日订单完全一致率：{result.summary['order_exact_day_match_rate']:.2%}。
- 首次持仓分叉：{result.summary['first_holding_mismatch_date'] or '无'}；首次订单分叉：{result.summary['first_order_mismatch_date'] or '无'}。

## 影子成交回执

{status_text}。

Qlib 在执行日可见当日可交易状态并可替补候选；影子模式在前一晚按当日收盘价锁定订单与股数，次日开盘不替补。本报告是这两条真实路径的黑盒对比，不使用兼容模式强行抹平差异。

明细见 `daily_compare.csv`、`holdings_compare.csv` 和 `orders_compare.csv`。
"""
    (output_dir / "report.md").write_text(report, encoding="utf-8")


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
        self._factors_by_date = None

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

    def factors_on(self, date: str) -> pd.Series:
        """返回截至指定日最近有效复权因子，覆盖停牌/退市持仓。"""
        if self._factors_by_date is None:
            self._factors_by_date = self.frame["factor"].unstack("instrument").ffill()
        timestamp = pd.Timestamp(date)
        if timestamp not in self._factors_by_date.index:
            raise ValueError(f"行情缓存缺少日期: {date}")
        return self._factors_by_date.loc[timestamp]

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


def _nav_metrics(values: pd.Series, initial_nav: float) -> dict:
    if values.empty:
        return {"total_return": None, "annualized_return": None, "max_drawdown": None}
    final_nav = float(values.iloc[-1])
    peak = values.cummax().clip(lower=initial_nav)
    total_return = final_nav / initial_nav - 1
    annualized = (
        (final_nav / initial_nav) ** (C.ANN / len(values)) - 1
        if final_nav > 0
        else None
    )
    return {
        "total_return": float(total_return),
        "annualized_return": None if annualized is None else float(annualized),
        "max_drawdown": float((values / peak - 1).min()),
    }


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
    holding_day_matches = []
    order_day_matches = []
    receipt_status_counts = Counter()
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
        holding_day_matches.append(qlib_snap.holdings == shadow_snap.holdings)
        order_day_matches.append(
            _aggregate_orders(qlib_snap.orders) == _aggregate_orders(shadow_snap.orders)
        )
        receipt_status_counts.update(shadow_snap.receipts.values())

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
    qlib_nav_metrics = _nav_metrics(daily["qlib_nav"], C.SHADOW_INIT_CASH)
    shadow_nav_metrics = _nav_metrics(daily["shadow_nav"], C.SHADOW_INIT_CASH)
    holding_mismatch_dates = [
        date for date, matched in zip(sorted(qlib_dates), holding_day_matches) if not matched
    ]
    order_mismatch_dates = [
        date for date, matched in zip(sorted(qlib_dates), order_day_matches) if not matched
    ]
    summary = {
        "daily_rows": len(daily),
        "qlib_final_nav": float(daily.iloc[-1]["qlib_nav"]) if not daily.empty else None,
        "shadow_final_nav": float(daily.iloc[-1]["shadow_nav"]) if not daily.empty else None,
        "max_abs_nav_delta": float(daily["nav_delta"].abs().max()) if not daily.empty else 0.0,
        "cash_match_rate": float(daily["cash_match"].mean()) if not daily.empty else 1.0,
        "nav_match_rate": float(daily["nav_match"].mean()) if not daily.empty else 1.0,
        "holding_match_rate": _match_rate(holdings),
        "order_match_rate": _match_rate(orders),
        "holding_exact_day_match_rate": (
            float(sum(holding_day_matches) / len(holding_day_matches))
            if holding_day_matches
            else 1.0
        ),
        "order_exact_day_match_rate": (
            float(sum(order_day_matches) / len(order_day_matches))
            if order_day_matches
            else 1.0
        ),
        "first_holding_mismatch_date": holding_mismatch_dates[0]
        if holding_mismatch_dates
        else None,
        "first_order_mismatch_date": order_mismatch_dates[0]
        if order_mismatch_dates
        else None,
        "gate_on_days": int(daily["gate_on"].sum()) if not daily.empty else 0,
        "gate_off_days": int((~daily["gate_on"]).sum()) if not daily.empty else 0,
        "shadow_receipt_status_counts": dict(sorted(receipt_status_counts.items())),
        "qlib_total_return": qlib_nav_metrics["total_return"],
        "qlib_annualized_return": qlib_nav_metrics["annualized_return"],
        "qlib_max_drawdown": qlib_nav_metrics["max_drawdown"],
        "shadow_total_return": shadow_nav_metrics["total_return"],
        "shadow_annualized_return": shadow_nav_metrics["annualized_return"],
        "shadow_max_drawdown": shadow_nav_metrics["max_drawdown"],
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

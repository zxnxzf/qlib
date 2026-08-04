"""影子回放与 Qlib 普通回测的同口径对账工具。"""

from dataclasses import dataclass
from typing import Dict, List, Tuple

import pandas as pd


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

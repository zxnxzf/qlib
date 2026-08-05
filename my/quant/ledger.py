"""账本：影子账户状态持久化（持仓/现金/订单/回执/净值/停摆日志）。"""

import csv
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from . import config as C
from .execution import Receipt
from .portfolio import Order
from .trade_planner import PlanSkip, PlannedOrder


def _p(name: str) -> Path:
    C.STATE_DIR.mkdir(parents=True, exist_ok=True)
    (C.STATE_DIR / "orders").mkdir(exist_ok=True)
    (C.STATE_DIR / "receipts").mkdir(exist_ok=True)
    (C.STATE_DIR / "skips").mkdir(exist_ok=True)
    (C.STATE_DIR / "signals").mkdir(exist_ok=True)
    return C.STATE_DIR / name


def load_state() -> dict:
    fp = _p("state.json")
    if fp.exists():
        state = json.loads(fp.read_text())
        state.setdefault("last_prices", {})
        state.setdefault("phase", "idle")
        state.setdefault("pending_batch_id", None)
        state.setdefault("pending_signal_date", None)
        return state
    return {
        "cash": C.SHADOW_INIT_CASH,
        "holdings": {},
        "last_prices": {},
        "last_settled": None,
        "pending_exec_date": None,
        "pending_signal_date": None,
        "pending_batch_id": None,
        "phase": "idle",
    }


def save_state(state: dict) -> None:
    path = _p("state.json")
    temporary = _p("state.json.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(state, ensure_ascii=False, indent=1))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def save_orders(exec_date: str, orders: List[Order]) -> None:
    fp = _p("orders") / f"{exec_date}.csv"
    with fp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "side", "shares", "ref_price", "reason"])
        for o in orders:
            w.writerow([o.code, o.side, o.shares, f"{o.ref_price:.3f}", o.reason])


def load_orders(exec_date: str) -> List[Order]:
    fp = _p("orders") / f"{exec_date}.csv"
    if not fp.exists():
        return []
    out = []
    with fp.open() as f:
        for row in csv.DictReader(f):
            out.append(Order(row["code"], row["side"], int(row["shares"]), float(row["ref_price"]), row["reason"]))
    return out


def save_receipts(exec_date: str, receipts: List[Receipt], stage: Optional[str] = None) -> None:
    suffix = f"_{stage}" if stage else ""
    fp = _p("receipts") / f"{exec_date}{suffix}.csv"
    with fp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "side", "shares", "price", "cost", "status"])
        for r in receipts:
            w.writerow([r.code, r.side, r.shares, f"{r.price:.3f}", f"{r.cost:.2f}", r.status])


def load_receipts(exec_date: str, stage: Optional[str] = None) -> List[Receipt]:
    suffix = f"_{stage}" if stage else ""
    fp = _p("receipts") / f"{exec_date}{suffix}.csv"
    if not fp.exists():
        return []
    with fp.open() as stream:
        return [
            Receipt(
                code=row["code"],
                side=row["side"],
                shares=int(row["shares"]),
                price=float(row["price"]),
                cost=float(row["cost"]),
                status=row["status"],
            )
            for row in csv.DictReader(stream)
        ]


def save_planned_orders(exec_date: str, stage: str, orders: List[PlannedOrder]) -> None:
    fp = _p("orders") / f"{exec_date}_{stage}.csv"
    with fp.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "order_id",
                "batch_id",
                "stage",
                "code",
                "side",
                "shares",
                "limit_price",
                "price_floor",
                "price_ceiling",
                "candidate_rank",
                "reason",
            ]
        )
        for index, order in enumerate(orders, start=1):
            writer.writerow(
                [
                    f"{order.batch_id}:{stage}:{index}",
                    order.batch_id,
                    stage,
                    order.code,
                    order.side,
                    order.shares,
                    f"{order.limit_price:.6f}",
                    f"{order.price_floor:.6f}",
                    f"{order.price_ceiling:.6f}",
                    "" if order.candidate_rank is None else order.candidate_rank,
                    order.reason,
                ]
            )


def load_planned_orders(exec_date: str, stage: str) -> List[PlannedOrder]:
    fp = _p("orders") / f"{exec_date}_{stage}.csv"
    if not fp.exists():
        return []
    with fp.open() as stream:
        return [
            PlannedOrder(
                code=row["code"],
                side=row["side"],
                shares=int(row["shares"]),
                limit_price=float(row["limit_price"]),
                reason=row["reason"],
                price_floor=float(row["price_floor"]),
                price_ceiling=float(row["price_ceiling"]),
                candidate_rank=int(row["candidate_rank"]) if row["candidate_rank"] else None,
                batch_id=row["batch_id"],
            )
            for row in csv.DictReader(stream)
        ]


def save_plan_skips(exec_date: str, stage: str, skips: List[PlanSkip]) -> None:
    fp = _p("skips") / f"{exec_date}_{stage}.csv"
    with fp.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["stage", "code", "side", "reason"])
        for skip in skips:
            writer.writerow([stage, skip.code, skip.side, skip.reason])


def append_nav(date: str, nav: float, cash: float, n_holdings: int, gate_on: bool, note: str = "") -> None:
    fp = _p("nav.csv")
    new = not fp.exists()
    with fp.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "nav", "cash", "n_holdings", "gate_on", "note"])
        w.writerow([date, f"{nav:.2f}", f"{cash:.2f}", n_holdings, int(gate_on), note])


def append_log(event: str) -> None:
    with _p("shadow.log").open("a") as f:
        import datetime

        f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {event}\n")

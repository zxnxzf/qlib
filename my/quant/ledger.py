"""账本：影子账户状态持久化（持仓/现金/订单/回执/净值/停摆日志）。"""

import csv
import json
from pathlib import Path
from typing import Dict, List, Optional

from . import config as C
from .execution import Receipt
from .portfolio import Order


def _p(name: str) -> Path:
    C.STATE_DIR.mkdir(parents=True, exist_ok=True)
    (C.STATE_DIR / "orders").mkdir(exist_ok=True)
    (C.STATE_DIR / "receipts").mkdir(exist_ok=True)
    return C.STATE_DIR / name


def load_state() -> dict:
    fp = _p("state.json")
    if fp.exists():
        state = json.loads(fp.read_text())
        state.setdefault("last_prices", {})
        return state
    return {
        "cash": C.SHADOW_INIT_CASH,
        "holdings": {},
        "last_prices": {},
        "last_settled": None,
        "pending_exec_date": None,
    }


def save_state(state: dict) -> None:
    _p("state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1))


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


def save_receipts(exec_date: str, receipts: List[Receipt]) -> None:
    fp = _p("receipts") / f"{exec_date}.csv"
    with fp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "side", "shares", "price", "cost", "status"])
        for r in receipts:
            w.writerow([r.code, r.side, r.shares, f"{r.price:.3f}", f"{r.cost:.2f}", r.status])


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

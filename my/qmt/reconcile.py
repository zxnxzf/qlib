"""Replay QMT planning from archived broker facts without touching either ledger."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence

from my.quant.trade_planner import plan_buys, plan_sells

from . import PLANNER_VERSION
from .protocol import read_json, read_result, signal_path, validate_signal
from .qmt_strategy import (
    _account_for_planner,
    _atomic_replace_json,
    _market_for_planner,
    _planned_order,
    package_from_signal,
)


SHANGHAI_TZ = timezone(timedelta(hours=8))
ORDER_FIELDS = (
    "order_id",
    "code",
    "side",
    "shares",
    "reference_price",
    "submit_price",
    "price_floor",
    "price_ceiling",
    "reason",
    "candidate_rank",
)


def _normalized_order(row: dict) -> dict:
    return {field: row.get(field) for field in ORDER_FIELDS}


def compare_plans(expected: Sequence[dict], actual: Sequence[dict]) -> dict:
    expected_rows = [_normalized_order(row) for row in expected]
    actual_rows = [_normalized_order(row) for row in actual]
    differences = []
    for index in range(max(len(expected_rows), len(actual_rows))):
        expected_row = expected_rows[index] if index < len(expected_rows) else None
        actual_row = actual_rows[index] if index < len(actual_rows) else None
        if expected_row != actual_row:
            differences.append({"index": index, "replayed": expected_row, "archived": actual_row})
    return {
        "match": not differences,
        "replayed_count": len(expected_rows),
        "archived_count": len(actual_rows),
        "differences": differences,
    }


def reconcile_day(runtime_root: Path, exec_date: str, output_path: Optional[Path] = None) -> dict:
    runtime_root = Path(runtime_root)
    raw_signal = read_json(signal_path(runtime_root, exec_date))
    signal = validate_signal(
        raw_signal,
        expected_exec_date=exec_date,
        expected_account_alias=raw_signal.get("account_alias"),
        expected_planner_version=PLANNER_VERSION,
        check_expiry=False,
    )
    result = read_result(
        runtime_root,
        exec_date,
        expected_batch_id=signal["batch_id"],
        expected_planner_version=PLANNER_VERSION,
    )
    sell_market = _market_for_planner(result["market_snapshot"], exec_date)
    buy_market = _market_for_planner(result["buy_market_snapshot"], exec_date)

    before = _account_for_planner(result["account_before"])
    sell_package = package_from_signal(signal, before.holdings)
    sell_plan = plan_sells(sell_package, before, sell_market)
    replayed_sells = [
        _planned_order(order, "%s:sell:%03d" % (signal["batch_id"], index))
        for index, order in enumerate(sell_plan.orders, start=1)
    ]

    after_sell = _account_for_planner(result["account_after_sell"])
    buy_package = package_from_signal(signal, after_sell.holdings)
    buy_plan = plan_buys(buy_package, after_sell, buy_market)
    replayed_buys = [
        _planned_order(order, "%s:buy:%03d" % (signal["batch_id"], index))
        for index, order in enumerate(buy_plan.orders, start=1)
    ]

    sell_comparison = compare_plans(replayed_sells, result["sell_stage"].get("planned", []))
    buy_comparison = compare_plans(replayed_buys, result["buy_stage"].get("planned", []))
    report = {
        "schema_version": 1,
        "batch_id": signal["batch_id"],
        "exec_date": exec_date,
        "planner_version": PLANNER_VERSION,
        "created_at": datetime.now(SHANGHAI_TZ).isoformat(),
        "match": sell_comparison["match"] and buy_comparison["match"],
        "sell": sell_comparison,
        "buy": buy_comparison,
        "read_only": True,
    }
    target = output_path or runtime_root / "qmt_outbox" / exec_date / "reconcile.json"
    _atomic_replace_json(Path(target), report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay one archived QMT plan from broker snapshots")
    parser.add_argument("exec_date")
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    report = reconcile_day(args.runtime_root, args.exec_date, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

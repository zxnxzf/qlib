#!/usr/bin/env python3
"""
Export daily positions from workflow artifacts to manual positions CSV.

Usage:
  python export_positions_from_workflow.py --artifacts /path/to/artifacts \\
      --dates 2025-01-06,2025-03-03,2025-05-20 --output positions_manual.csv
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, List

import pandas as pd

import qlib
from qlib.data import D


def _parse_dates(dates_str: str) -> List[str]:
    parts = [d.strip() for d in dates_str.split(",") if d.strip()]
    if not parts:
        raise ValueError("dates cannot be empty")
    return [pd.Timestamp(d).strftime("%Y-%m-%d") for d in parts]


def _load_positions_dict(artifacts_path: Path) -> Dict[pd.Timestamp, object]:
    pos_path = artifacts_path / "portfolio_analysis" / "positions_normal_1day.pkl"
    if not pos_path.exists():
        raise FileNotFoundError(f"positions file not found: {pos_path}")
    return pd.read_pickle(pos_path)


def _get_factor_map(codes: List[str], date_str: str) -> Dict[str, float]:
    if not codes:
        return {}
    try:
        df = D.features(codes, ["$factor"], start_time=date_str, end_time=date_str)
    except Exception:
        df = pd.DataFrame()
    if df is None or df.empty:
        return {code: 1.0 for code in codes}
    factor = df.reset_index().rename(columns={"$factor": "factor"})
    factor = factor.dropna(subset=["factor"])
    return {str(row["instrument"]): float(row["factor"]) for _, row in factor.iterrows()}


def _position_to_rows(position, date_str: str) -> List[Dict[str, float]]:
    stock_list = position.get_stock_list()
    factor_map = _get_factor_map(stock_list, date_str)
    rows = []
    for code in stock_list:
        amount_adj = float(position.get_stock_amount(code))
        factor = factor_map.get(code, 1.0)
        raw = amount_adj * factor
        rows.append({"code": code, "position": int(raw)})
    return rows


def _export_positions(
    artifacts_path: Path,
    date_str: str,
    output_path: Path,
    provider_uri: str,
    region: str,
) -> None:
    qlib.init(provider_uri=provider_uri, region=region)
    positions_dict = _load_positions_dict(artifacts_path)
    target_date = pd.Timestamp(date_str)
    if target_date not in positions_dict:
        raise KeyError(f"date {date_str} not found in positions_normal_1day.pkl")
    position = positions_dict[target_date]
    cash = float(position.get_cash())
    rows = [{"code": "CASH", "position": cash}]
    rows.extend(_position_to_rows(position, date_str))
    df = pd.DataFrame(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export manual positions from workflow artifacts.")
    parser.add_argument("--artifacts", required=True, help="Path to mlruns run artifacts directory.")
    parser.add_argument("--dates", required=True, help="Comma-separated dates, e.g. 2025-01-06,2025-03-03")
    parser.add_argument("--output", required=True, help="Output CSV path (will be overwritten per date).")
    parser.add_argument("--provider-uri", default="/Users/bytedance/code/qlib/my/data/cn_data")
    parser.add_argument("--region", default="cn")
    args = parser.parse_args()

    artifacts_path = Path(args.artifacts).expanduser().resolve()
    dates = _parse_dates(args.dates)
    output_path = Path(args.output).expanduser().resolve()

    for date_str in dates:
        _export_positions(
            artifacts_path=artifacts_path,
            date_str=date_str,
            output_path=output_path,
            provider_uri=args.provider_uri,
            region=args.region,
        )
        print(f"[OK] {date_str} positions saved: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

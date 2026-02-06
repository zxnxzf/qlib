# Manual vs Workflow Consistency Check (2025-01-01 to 2025-01-31)

## Context
We already have `manual_daily_trade.py` aligned to the LightGBM Alpha158 workflow. We want to extend the validation period to the end of January 2025 and verify that manual daily execution matches workflow backtest positions when starting from cash.

## Goals
- Run a workflow ending on `2025-01-31` with the same config as the existing short-range run.
- Use the new workflow recorder as the source of predictions and backtest window for `manual_daily_trade.py`.
- Compare daily positions from `manual_daily_trade.py` against workflow positions for each trading day in the report window.
- Allow only minimal rounding differences (±1 share) and report any deviations with dates and symbols.

## Approach
1. Create a new workflow config file by copying the short-range config and updating `end_time` fields to `2025-01-31`.
2. Run the workflow via `qlib.cli.run` to generate a new recorder.
3. Update `manual_daily_trade.py` to point at the new `recorder_id`.
4. For each trading day in the workflow report window:
   - Start with `positions_manual.csv` as cash-only on the first day.
   - Run `manual_daily_trade.py --trade-date <day>`.
   - Compare `positions_manual_next.csv` with workflow positions for the same day.
   - Carry forward the manual next positions to the next day.
5. Summarize results (diff counts, max per-symbol diffs, cash differences).

## Validation Criteria
- Cash difference should be 0.
- Per-symbol share differences should be 0 or ±1 (rounding).
- Any larger diff is a mismatch and must be reported with date + symbol.

## Outputs
- New workflow config file under `examples/custom/`.
- Console comparison summary (per-day diff stats).
- Updated `manual_daily_trade.py` recorder ID.

## Risks
- Long data processing time for the workflow.
- Missing predictions on some days; in that case manual positions should carry forward and still match workflow.

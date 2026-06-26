# Task Plan: Manual Daily Trading Script

## Objective
Implement a custom daily (manual) trading workflow script under `examples/custom` that:
- uses the prior trading day data for predictions
- outputs order CSV and console summary
- consumes holdings/cash CSV provided by the user
- handles trading-day checks without relying on Qlib's local calendar

## Phases
- [x] Phase 1: Planning and setup
- [x] Phase 2: Research / decisions
- [x] Phase 3: Build script and docs
- [x] Phase 4: Review and handoff

## Key Questions
1. Which trading calendar source should we use (local CSV, tushare token, or manual trade_date input)?
2. What is the script filename under `examples/custom`?
3. Confirm final input/output CSV schemas.
4. If using local CSV, will the user provide an official trading-day list or accept an approximation?

## Decisions Made
- Rolled back akshare installation attempts and reverted `packaging` to 21.3.
- Use Qlib data up to the last available trading day for `pred_date`; `trade_date` is "today".
- Use a local weekday-only calendar CSV for 2026 (no holiday adjustments).
- Data update check is based on local calendar; after update attempt, proceed without strict required-date coverage check.

## Errors Encountered
- akshare requires `aiohttp>=3.11.13` which is incompatible with Python 3.8.
- exchange_calendars XSHG calendar only covers through 2025, causing DateOutOfBounds for 2026.

## Status
**Complete** - manual script implemented and configured.

---

# Task Plan: Downloader Startup Cleanup for manual_daily_trade

## Objective
When data auto-update is triggered, delete stale `~/.qlib/temp/qlib_bin.tar.gz` once at the beginning of the update cycle, while keeping resume (`curl -C -`) for retries within the same run.

## Phases
- [x] Phase 1: Planning and setup
- [x] Phase 2: Implement startup cleanup in downloader
- [x] Phase 3: Validate behavior and syntax
- [x] Phase 4: Handoff

## Key Questions
1. Should resume be kept? (Yes)
2. Cleanup timing? (Once at update-cycle start)

## Decisions Made
- Keep resume support for retries inside one update cycle.
- Add one-time pre-cleanup before the first download attempt in `_download_and_update_data`.

## Errors Encountered
- None.

## Status
**Complete** - startup cleanup added; syntax check passed.

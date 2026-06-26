# Notes: Manual Daily Trading Script

## Sources

### Qlib examples
- `examples/live_daily_predict.py`: reference behavior; aligns to available calendar data.

### Calendar options
- `exchange_calendars`: XSHG only covers through 2025 in current package.

## Findings
- Qlib local calendar only includes dates up to the latest available data.
- External calendar or manual trade_date input is required for "today" checks.
- Cannot generate a future year trading calendar without an external data source; exchange_calendars ends at 2025.
- Practical local CSV approach: generate weekday calendar, then apply manual overrides (holidays/off-days removed, make-up trading days added).
- Generated weekday-only calendar for 2026 at `examples/custom/trade_calendar_base.csv` (261 rows).
- Data update rule: run update if needed; do not block on required pred_date coverage because local calendar is approximate.

## Update 2026-03-04
- `manual_daily_trade.py` downloader now cleans stale `~/.qlib/temp/qlib_bin.tar.gz` once when a new auto-update cycle starts.
- Resume download (`curl -C -`) is still enabled for retries within the same run.
- Existing fallback remains: on resume-related failure, retry full download; if curl still fails, fallback to urllib.

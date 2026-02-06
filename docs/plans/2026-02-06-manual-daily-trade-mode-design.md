# Manual Daily Trade: workflow alignment mode

## Goal
Add a simple `mode` switch to `examples/custom/manual_daily_trade.py` so the script can run in two consistent behaviors:
- `align`: maximize parity with the backtest workflow (use workflow artifacts when available).
- `live`: keep the same market/handler/strategy/cost alignment, but avoid depending on backtest artifacts so the script works for day‑by‑day manual execution.

## Constraints and assumptions
- We keep single‑day prediction behavior (trade date is one day; pred date is last available trading date).
- The same workflow config (`workflow_config_lightgbm_Alpha158_2020_2025.yaml`) remains the reference for alignment.
- “Live” should still use Qlib local data and model params; it must not require `pred.pkl` or workflow position counts.

## Approach
Introduce `workflow_alignment.mode` with values `align` and `live`.
- `align` uses `pred.pkl` (when available), workflow position counts, and the workflow backtest window for Exchange initialization.
- `live` disables those dependencies but still uses the same market/handler window and trading cost/limit settings, so the signals and order generation logic stay consistent with the workflow.

A single set of “effective flags” is computed once in `main` and logged clearly. Invalid `mode` falls back to `align` with a warning to avoid silent misconfiguration.

## Data flow changes
- Compute effective flags: `use_required_pred_date`, `use_recorder_pred`, `use_position_count`, `use_backtest_window`, `use_execution_simulator`.
- If `mode=live`, force `use_recorder_pred/use_position_count/use_backtest_window` to `False`.
- Use the effective flags in the prediction load path, position count loading, and backtest window selection.

## Error handling
- Unknown `mode` emits a warning and defaults to `align`.
- If workflow artifacts are unavailable in `align`, existing fallbacks remain (e.g., generate predictions, ignore position count).

## Testing plan
- Run `manual_daily_trade.py` once in `align` mode and verify logs show workflow artifacts used.
- Run in `live` mode and verify no `pred.pkl`/position count/backtest window is loaded.
- Compare positions over a short window (e.g., 2025‑01‑02 to 2025‑01‑27) to ensure `align` stays consistent with workflow outputs.

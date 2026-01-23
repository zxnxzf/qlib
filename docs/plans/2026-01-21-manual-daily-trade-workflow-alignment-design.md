# Manual Daily Trade Workflow Alignment Design

## Goal
Align `manual_daily_trade.py` with the existing LightGBM Alpha158 workflow configuration so the live, single-day
prediction and order generation uses the same stock universe and the same feature normalization window as the
backtest workflow. The script must remain single-day (pred_date only) and keep the existing manual order output
behavior. The workflow configuration will be hard-coded in the script for now (no YAML parsing), because the user
only needs this one workflow at the moment.

## Scope
- Align the stock universe with `market: all`.
- Align the handler time window with `data_handler_config`:
  - `start_time = 2020-01-01`
  - `fit_start_time = 2020-01-01`
  - `fit_end_time = 2022-12-31`
- Preserve single-day prediction: `segments.test` remains a single date (pred_date).
- Keep `end_time` dynamic (pred_date) to avoid truncating 2026 inference data.

## Approach
Add a dedicated `workflow_alignment` block in `DEFAULT_CONFIG` to keep workflow-related overrides visually grouped.
When enabled, the script overwrites the prediction config at runtime:
- `prediction.instruments` is set to the workflow market (`all`).
- `prediction.handler_kwargs` is populated with the workflow time window fields.
- `end_time` is not forced unless explicitly set to `pred_date/auto`.

The existing `dataset_config()` function already merges `handler_kwargs` on top of the default time window, so
workflow overrides will take effect without changing the single-day segment logic.

## Logging
Add a short info log when workflow alignment is enabled to make it obvious which market and handler window are used.

## Risks
- Using `market: all` increases memory and run time vs `csi300`.
- If the workflow handler window is mismatched with model training data, results may differ.

## Testing
Run the script once and confirm:
- The log shows workflow alignment parameters.
- `pred_date` is still a single date.
- Predictions are non-empty and orders are generated.

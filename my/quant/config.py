"""Runtime facade over the versioned strategy package.

Existing shadow/backtest modules keep importing ``my.quant.config`` while the
canonical model, rolling, gate, and execution parameters live together under
``my/strategies/<strategy_id>/``.
"""

import os
from pathlib import Path

import yaml


# ---------- repository and strategy package ----------
REPO = Path(__file__).resolve().parents[2]
STRATEGY_ID = "lgb_alpha158_gate905_v1"
STRATEGY_DIR = REPO / "my" / "strategies" / STRATEGY_ID


def _load_config(name: str) -> dict:
    path = STRATEGY_DIR / name
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RuntimeError(f"策略配置版本无效: {path}")
    if payload.get("strategy_id") != STRATEGY_ID:
        raise RuntimeError(f"策略配置 strategy_id 不一致: {path}")
    return payload


WORKFLOW_CONFIG = _load_config("workflow.yaml")
ROLLING_CONFIG = _load_config("rolling.yaml")
STRATEGY_CONFIG = _load_config("strategy.yaml")

# ---------- paths (repository-relative on macOS and Windows) ----------
_provider_uri = Path(WORKFLOW_CONFIG["data"]["provider_uri"])
if _provider_uri.is_absolute():
    raise RuntimeError("workflow.yaml 的 provider_uri 必须相对仓库根目录")
DATA_DIR = REPO / _provider_uri
ARTIFACTS = REPO / "my" / "artifacts"
STATE_DIR = REPO / "my" / "quant_state"  # mutable shadow ledger (gitignored)
MODEL_DIR = STRATEGY_DIR / "models"  # reviewed native Boosters (tracked by Git)
RELEASE_DIR = STRATEGY_DIR / "releases"
VENV_PY = str(REPO / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
UPDATE_SCRIPT = str(REPO / "my" / "scripts" / "update_research_data.py")

# ---------- market ----------
POOL = str(WORKFLOW_CONFIG["data"]["pool"])
BENCH = str(WORKFLOW_CONFIG["data"]["benchmark"])
GATE_INDEX = str(STRATEGY_CONFIG["gate"]["index"])
GATE_INDEX_NAME = str(STRATEGY_CONFIG["gate"]["index_name"])
ANN = int(STRATEGY_CONFIG["performance"]["annualization_days"])

# ---------- portfolio and execution ----------
TOPK = int(STRATEGY_CONFIG["portfolio"]["topk"])
CANDIDATE_LIMIT = int(STRATEGY_CONFIG["portfolio"]["candidate_limit"])
N_DROP = int(STRATEGY_CONFIG["portfolio"]["n_drop"])
HOLD_THRESH = int(STRATEGY_CONFIG["portfolio"]["hold_thresh"])
ONLY_TRADABLE = bool(STRATEGY_CONFIG["portfolio"]["only_tradable"])
RISK_DEGREE = float(STRATEGY_CONFIG["portfolio"]["risk_degree"])
LOT = int(STRATEGY_CONFIG["portfolio"]["lot_size"])

DEAL_PRICE = str(STRATEGY_CONFIG["execution"]["deal_price"])
EXECUTION_WAIT_SECONDS = int(STRATEGY_CONFIG["execution"]["wait_seconds"])
LIMIT_TH = float(STRATEGY_CONFIG["execution"]["limit_threshold"])
MAX_SLIPPAGE = float(STRATEGY_CONFIG["execution"]["max_slippage"])

# ---------- gate ----------
GATE_MA = int(STRATEGY_CONFIG["gate"]["ma_window"])
GATE_CONFIRM_DAYS = int(STRATEGY_CONFIG["gate"]["confirm_days"])
GATE_SURGE_REENTRY = float(STRATEGY_CONFIG["gate"]["surge_reentry"])

# ---------- costs ----------
OPEN_COST = float(STRATEGY_CONFIG["costs"]["open_cost"])
CLOSE_COST = float(STRATEGY_CONFIG["costs"]["close_cost"])
MIN_COST = float(STRATEGY_CONFIG["costs"]["min_cost"])
IMPACT_COST = float(STRATEGY_CONFIG["costs"]["impact_cost"])

# ---------- rolling model ----------
LABEL_EXPR = str(WORKFLOW_CONFIG["handler"]["label_expr"])
TRAIN_MONTHS = int(ROLLING_CONFIG["schedule"]["total_lookback_months"])
VALID_MONTHS = int(ROLLING_CONFIG["schedule"]["validation_months"])
VALID_GAP_DAYS = int(ROLLING_CONFIG["schedule"]["validation_gap_calendar_days"])
LGB_PARAMS = dict(WORKFLOW_CONFIG["model"]["params"])
NUM_BOOST_ROUND = int(WORKFLOW_CONFIG["model"]["num_boost_round"])
EARLY_STOP = int(WORKFLOW_CONFIG["model"]["early_stopping_rounds"])

# ---------- shadow account ----------
SHADOW_INIT_CASH = float(STRATEGY_CONFIG["account"]["shadow_initial_cash"])

"""全局口径与路径——所有可调参数集中于此，改配置不改代码。"""

from pathlib import Path

# ---------- 路径 ----------
REPO = Path("/Users/bytedance/code/qlib")
DATA_DIR = REPO / "my" / "data" / "cn_data"
ARTIFACTS = REPO / "my" / "artifacts"
STATE_DIR = REPO / "my" / "quant_state"          # 影子/实盘账本（gitignore）
MODEL_DIR = STATE_DIR / "models"                  # 季度模型
VENV_PY = str(REPO / ".venv" / "bin" / "python")
UPDATE_SCRIPT = str(REPO / "my" / "scripts" / "update_research_data.py")

# ---------- 市场口径 ----------
POOL = "all_no_bj"
BENCH = "SH000300"
GATE_INDEX = "SH000905"          # 门控温度计：中证500（SH000985 上游已断更）
GATE_INDEX_NAME = "中证500"
ANN = 238

# ---------- 候选策略参数（2026-08 定版：开盘执行 + n_drop2 + 门控V2） ----------
TOPK = 50
N_DROP = 2
RISK_DEGREE = 0.95               # 买入预算占现金比例
LOT = 100                        # 一手股数

# 门控 V2：连续 CONFIRM_DAYS 天收于 MA_WINDOW 均线下方→离场；单日涨幅>SURGE_REENTRY→强制回场
GATE_MA = 20
GATE_CONFIRM_DAYS = 3
GATE_SURGE_REENTRY = 0.025

# ---------- 成本口径（研究与影子一致） ----------
OPEN_COST = 0.0005
CLOSE_COST = 0.0015
MIN_COST = 5.0
IMPACT_COST = 0.001              # 现实滑点（单边），影子成交按 开盘价*(1±impact)
LIMIT_TH = 0.095                 # 开盘口径涨跌停判定阈值

# ---------- 模型口径（与研究流水线一致） ----------
LABEL_EXPR = "Ref($close, -6) / Ref($close, -1) - 1"     # 5日label
TRAIN_MONTHS = 42                # 训练窗（含验证段）
VALID_MONTHS = 6
VALID_GAP_DAYS = 7
LGB_PARAMS = dict(
    objective="mse", colsample_bytree=0.8879, learning_rate=0.0421, subsample=0.8789,
    lambda_l1=205.6999, lambda_l2=580.9768, max_depth=8, num_leaves=210,
    num_threads=8, verbosity=-1,
)
NUM_BOOST_ROUND = 2000
EARLY_STOP = 100

# ---------- 影子账户 ----------
SHADOW_INIT_CASH = 100_000.0

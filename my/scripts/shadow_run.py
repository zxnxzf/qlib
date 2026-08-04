#!/usr/bin/env python3
"""影子模式入口（薄壳）。

用法:
  shadow_run.py nightly                 # 每晚正式运行（更新数据→结算→出单）
  shadow_run.py backfill A B            # 历史回填验证：对 [A,B] 区间逐个交易日跑（不更新数据）
  shadow_run.py status                  # 打印账户状态与最近净值
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/Users/bytedance/code/qlib")

from my.quant import config as C  # noqa: E402
from my.quant import data, ledger, nightly  # noqa: E402


def _configure_backfill_state(start: str, end: str, run_id: str = "") -> Path:
    """把历史回填账本隔离到独立目录，绝不读写正式影子账户。"""
    run_id = run_id or datetime.now().strftime("%Y%m%dT%H%M%S")
    selected = C.STATE_DIR / "backfills" / f"{start}_{end}_{run_id}"
    C.STATE_DIR = selected
    return selected


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "nightly"
    if cmd == "nightly":
        nightly.run_evening()
    elif cmd == "backfill":
        a, b = sys.argv[2], sys.argv[3]
        state_dir = _configure_backfill_state(a, b)
        print(f"[backfill] 独立账本: {state_dir}")
        data.init_qlib()
        days = [d for d in data.calendar() if a <= d <= b]
        for d in days:
            nightly.run_evening(asof=d, skip_update=True)
    elif cmd == "status":
        state = ledger.load_state()
        print(f"现金: {state['cash']:,.2f}  持仓: {len(state['holdings'])} 只  待结算: {state.get('pending_exec_date')}")
        nav = C.STATE_DIR / "nav.csv"
        if nav.exists():
            print("最近净值:")
            print("\n".join(nav.read_text().strip().splitlines()[-6:]))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

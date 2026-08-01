#!/usr/bin/env python3
"""更新本机研究数据到最新：复用 manual_daily_trade 的下载链路
（manifest 前置校验 + curl 断点续传 + sha256 + 日历硬闸 + 更新后复查）。

用法：
  /Users/bytedance/code/qlib/.venv/bin/python /Users/bytedance/code/qlib/my/artifacts/update_research_data.py
"""

import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, "/Users/bytedance/code/qlib/my/trading")
sys.path.insert(0, "/Users/bytedance/code/qlib/my/trading")
sys.path.insert(0, "/Users/bytedance/code/qlib")

import pandas as pd  # noqa: E402
from pandas.tseries.offsets import BDay  # noqa: E402
import manual_daily_trade as mdt  # noqa: E402

DATA_DIR = Path("/Users/bytedance/code/qlib/my/data")
CN_DATA = DATA_DIR / "cn_data"
BACKUP = DATA_DIR / "cn_data_sample2020.bak"

URLS = [
    "https://ghfast.top/https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz",
    "https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz",
]


def main() -> int:
    # 旧样例数据先备份（仅首次）
    latest = mdt._latest_calendar_date(CN_DATA)
    if latest and latest < "2021-01-01" and not BACKUP.exists():
        shutil.copytree(CN_DATA, BACKUP)
        print(f"[backup] 旧样例数据已备份 -> {BACKUP}")

    today = date.today().strftime("%Y-%m-%d")
    required = (pd.Timestamp(today) - BDay(1)).strftime("%Y-%m-%d")
    cfg = mdt.DataUpdateConfig(
        enable_auto_update=True,
        data_source_url=URLS[0],
        data_source_urls=URLS,
        download_timeout=1800,
        retry_count=2,
        retry_interval=5,
        temp_dir=str(DATA_DIR / "tmp_update"),
    )
    final_required = mdt._ensure_data_ready(str(CN_DATA), "csi300", today, required, cfg)
    print(f"[done] 数据日历末日期: {mdt._latest_calendar_date(CN_DATA)}, required={final_required}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

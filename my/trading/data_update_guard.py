#!/usr/bin/env python3
"""
data_update_guard：investment_data 数据包更新的硬校验工具。

被 live_daily_predict.py 与 examples/custom/manual_daily_trade.py 共用，提供：
1. 下载前：拉取 release 附带的 qlib_bin.manifest.json，校验上游是否覆盖所需的前一交易日；
2. 下载后：按 manifest 校验压缩包大小与 sha256；
3. 安装前：校验解压出的 calendars/day.txt 末日期是否覆盖所需交易日（最终硬闸，
   即使上游没有 manifest 也生效）；
4. 交易日历工具：合并 day.txt 与 day_future.txt 推算前一交易日，替代 BDay 近似。

manifest 字段（chenditc/investment_data 2026-07 起提供）：
    target_trade_date  数据覆盖到的最后一个交易日
    future_start_date  target 之后的下一个交易日（按真实交易所日历计算）
    archive_sha256     形如 "sha256:<hex>"
    archive_size_bytes 压缩包字节数
"""

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

MANIFEST_FILENAME = "qlib_bin.manifest.json"
ARCHIVE_FILENAME = "qlib_bin.tar.gz"
_FETCH_TIMEOUT = 30


class ArchiveRejectedError(Exception):
    """下载内容与 manifest 不符或未覆盖所需交易日；重试同一 URL 无意义，应换源。"""


def _repo_key(url: str) -> str:
    """提取 URL 中的 github owner/repo 作为 manifest 配对键；识别不了时退回整个前缀。"""
    marker = "github.com/"
    idx = url.find(marker)
    if idx >= 0:
        parts = url[idx + len(marker):].split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    return url.rsplit("/", 1)[0]


def derive_manifest_url(archive_url: str) -> Optional[str]:
    if archive_url.endswith(ARCHIVE_FILENAME):
        return archive_url[: -len(ARCHIVE_FILENAME)] + MANIFEST_FILENAME
    return None


def fetch_manifests(archive_urls: List[str], timeout: int = _FETCH_TIMEOUT) -> Dict[str, dict]:
    """按仓库聚合抓取 manifest；同仓库取 target_trade_date 最新的一份。

    返回 {repo_key: manifest}；一个 manifest 都抓不到时返回空 dict（调用方降级）。
    """
    results: Dict[str, dict] = {}
    for url in archive_urls:
        manifest_url = derive_manifest_url(url)
        if not manifest_url:
            continue
        try:
            req = Request(manifest_url, headers={"User-Agent": "qlib-data-guard"})
            with urlopen(req, timeout=timeout) as resp:
                manifest = json.loads(resp.read().decode("utf-8"))
        except Exception as err:
            print(f"[manifest] 获取失败（将降级为下载后校验）: {manifest_url}: {err}")
            continue
        if not isinstance(manifest, dict) or not manifest.get("target_trade_date"):
            print(f"[manifest] 内容缺少 target_trade_date，忽略: {manifest_url}")
            continue
        key = _repo_key(url)
        old = results.get(key)
        if old is None or str(manifest["target_trade_date"]) > str(old.get("target_trade_date", "")):
            results[key] = manifest
    return results


def best_manifest(manifests: Dict[str, dict]) -> Optional[dict]:
    if not manifests:
        return None
    return max(manifests.values(), key=lambda m: str(m.get("target_trade_date", "")))


def manifest_for_url(manifests: Dict[str, dict], archive_url: str) -> Optional[dict]:
    return manifests.get(_repo_key(archive_url))


def check_manifest_covers(
    manifest: dict,
    target_date: Optional[str],
    required_date: Optional[str],
) -> Tuple[bool, str]:
    """判断上游数据是否覆盖所需的前一交易日。

    优先用 future_start_date 判断：它由上游按真实交易所日历生成，
    target_date <= future_start_date 等价于 “target 前一交易日 <= target_trade_date”，
    对节假日免疫，也不依赖本地日历是否新鲜。
    """
    tgt = manifest.get("target_trade_date")
    fut = manifest.get("future_start_date")
    if fut and target_date:
        if str(target_date) <= str(fut):
            return True, f"target_trade_date={tgt}, future_start_date={fut}, 覆盖交易日 {target_date}"
        return False, (
            f"上游数据只到 target_trade_date={tgt}（下一交易日 {fut}），"
            f"不足以支撑交易日 {target_date}"
        )
    if tgt and required_date:
        if str(tgt) >= str(required_date):
            return True, f"target_trade_date={tgt} >= 所需前一交易日 {required_date}"
        return False, f"target_trade_date={tgt} < 所需前一交易日 {required_date}"
    return False, "manifest 缺少 target_trade_date/future_start_date，无法判断覆盖范围"


def sha256_of_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive_against_manifest(path: Path, manifest: dict) -> Tuple[bool, str]:
    """校验下载文件的大小与 sha256；manifest 缺字段时对应项跳过。"""
    expected_size = manifest.get("archive_size_bytes")
    if expected_size is not None:
        actual_size = path.stat().st_size
        if int(actual_size) != int(expected_size):
            return False, f"大小不符: 实际 {actual_size} != manifest {expected_size}"
    expected_sha = manifest.get("archive_sha256")
    if expected_sha:
        expected_hex = str(expected_sha).split(":", 1)[-1].strip().lower()
        actual_hex = sha256_of_file(path)
        if actual_hex != expected_hex:
            return False, f"sha256 不符: 实际 {actual_hex} != manifest {expected_hex}"
        return True, f"sha256 校验通过 ({actual_hex[:12]}...)"
    return True, "manifest 无 sha256 字段，仅通过大小校验"


def load_calendar_dates(data_path: Path) -> List[str]:
    """合并 calendars/day.txt 与 day_future.txt（若存在），升序去重。"""
    dates: List[str] = []
    for name in ("day.txt", "day_future.txt"):
        cal_file = data_path / "calendars" / name
        if not cal_file.exists():
            continue
        with cal_file.open("r") as f:
            for line in f:
                value = line.strip()
                if value:
                    dates.append(value)
    return sorted(set(dates))


def previous_trade_date_before(target_date: str, calendar_dates: List[str]) -> Optional[str]:
    """返回日历中严格早于 target_date 的最后一个交易日；无则返回 None。"""
    prev = None
    for d in calendar_dates:
        if d < target_date:
            prev = d
        else:
            break
    return prev


def latest_calendar_date(data_path: Path) -> Optional[str]:
    cal_file = data_path / "calendars" / "day.txt"
    if not cal_file.exists():
        return None
    with cal_file.open("r") as f:
        dates = [line.strip() for line in f if line.strip()]
    return dates[-1] if dates else None

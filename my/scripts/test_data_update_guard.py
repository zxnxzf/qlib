#!/usr/bin/env python3
"""数据更新硬校验的验证脚本（不下载真实 558MB 数据包）。

覆盖场景：
- data_update_guard 纯函数：URL 推导、覆盖判断、sha256/大小校验、交易日历推算
- live_daily_predict._ensure_data_ready / manual_daily_trade._ensure_data_ready 端到端：
  用本地构造的迷你 qlib_bin.tar.gz + file:// 数据源模拟 全新安装 / 上游未就绪 /
  sha 不符 / 无 manifest 降级 / 数据包未覆盖 / allow_stale_data 放行
- 真实网络：chenditc 官方源 manifest 可抓取，jzhongsun fork 404 优雅降级
"""

import json
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parents[1]
for p in (str(_REPO_ROOT), str(_REPO_ROOT / "my" / "trading"), str(_REPO_ROOT / "my" / "trading")):
    if p not in sys.path:
        sys.path.insert(0, p)

import data_update_guard as guard  # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def build_package(workdir: Path, calendar_dates, future_dates=None, instruments_name="csi300"):
    """构造迷你 qlib_bin.tar.gz + 对应 manifest，返回 (archive_path, manifest_path)。"""
    pkg = workdir / "qlib_bin"
    if pkg.exists():
        shutil.rmtree(pkg)
    (pkg / "features" / "sh600000").mkdir(parents=True)
    (pkg / "features" / "sh600000" / "close.day.bin").write_bytes(b"\x00" * 8)
    (pkg / "instruments").mkdir()
    (pkg / "instruments" / f"{instruments_name}.txt").write_text("SH600000\t2000-01-01\t2099-12-31\n")
    (pkg / "instruments" / "all.txt").write_text("SH600000\t2000-01-01\t2099-12-31\n")
    (pkg / "calendars").mkdir()
    (pkg / "calendars" / "day.txt").write_text("\n".join(calendar_dates) + "\n")
    if future_dates:
        (pkg / "calendars" / "day_future.txt").write_text("\n".join(future_dates) + "\n")
    archive = workdir / "qlib_bin.tar.gz"
    if archive.exists():
        archive.unlink()
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(pkg, arcname="qlib_bin")
    manifest = {
        "release_tag": calendar_dates[-1],
        "target_trade_date": calendar_dates[-1],
        "future_start_date": (future_dates[0] if future_dates else None),
        "archive_size_bytes": archive.stat().st_size,
        "archive_sha256": "sha256:" + guard.sha256_of_file(archive),
    }
    manifest_path = workdir / "qlib_bin.manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return archive, manifest_path


def test_pure_functions():
    print("== data_update_guard 纯函数 ==")
    check(
        "derive_manifest_url",
        guard.derive_manifest_url("https://x/y/qlib_bin.tar.gz") == "https://x/y/qlib_bin.manifest.json"
        and guard.derive_manifest_url("https://x/y/other.tar.gz") is None,
    )
    check(
        "repo_key github",
        guard._repo_key("https://ghfast.top/https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz")
        == "chenditc/investment_data",
    )
    check("repo_key fallback", guard._repo_key("file:///tmp/a/qlib_bin.tar.gz") == "file:///tmp/a")

    m = {"target_trade_date": "2026-07-24", "future_start_date": "2026-07-27"}
    ok1, _ = guard.check_manifest_covers(m, "2026-07-27", "2026-07-24")  # 周一交易，上游覆盖到上周五
    ok2, _ = guard.check_manifest_covers(m, "2026-07-28", "2026-07-27")  # 需要 7-27 数据但上游没有
    check("covers: 正常覆盖", ok1)
    check("covers: 上游滞后拒绝", not ok2)
    # 节假日免疫：假设 10-01~10-08 休市，target 10-09，required 若用 BDay 会误推 10-08
    m2 = {"target_trade_date": "2026-09-30", "future_start_date": "2026-10-09"}
    ok3, _ = guard.check_manifest_covers(m2, "2026-10-09", "2026-10-08")
    check("covers: 长假后 future_start 免疫", ok3)

    dates = ["2026-07-23", "2026-07-24", "2026-07-27"]
    check("previous_trade_date_before", guard.previous_trade_date_before("2026-07-27", dates) == "2026-07-24")
    check("previous_trade_date_before 无更早", guard.previous_trade_date_before("2026-07-20", dates) is None)

    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "x.bin"
        f.write_bytes(b"hello")
        good = {"archive_size_bytes": 5, "archive_sha256": "sha256:" + guard.sha256_of_file(f)}
        ok, _ = guard.verify_archive_against_manifest(f, good)
        check("verify_archive: 通过", ok)
        ok, detail = guard.verify_archive_against_manifest(f, {"archive_size_bytes": 6})
        check("verify_archive: 大小不符", not ok and "大小不符" in detail)
        ok, detail = guard.verify_archive_against_manifest(f, {"archive_size_bytes": 5, "archive_sha256": "sha256:" + "0" * 64})
        check("verify_archive: sha 不符", not ok and "sha256 不符" in detail)

        cal_dir = Path(td) / "prov" / "calendars"
        cal_dir.mkdir(parents=True)
        (cal_dir / "day.txt").write_text("2026-07-23\n2026-07-24\n")
        (cal_dir / "day_future.txt").write_text("2026-07-27\n2026-07-28\n")
        merged = guard.load_calendar_dates(Path(td) / "prov")
        check("load_calendar_dates 合并", merged == ["2026-07-23", "2026-07-24", "2026-07-27", "2026-07-28"])


def _live_cfg(live, urls, temp_dir, **kw):
    return live.DataUpdateConfig(
        enable_auto_update=kw.get("enable_auto_update", True),
        allow_stale_data=kw.get("allow_stale_data", False),
        data_source_url=urls[0],
        data_source_urls=urls,
        retry_count=1,
        retry_interval=0,
        temp_dir=str(temp_dir),
    )


def test_live_ensure_data_ready():
    print("== live_daily_predict._ensure_data_ready 端到端 ==")
    import live_daily_predict as live

    target = "2026-07-28"  # 周二；前一交易日 2026-07-27（BDay 兜底同样得 7-27）
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = td / "src"
        src.mkdir()
        provider = td / "cn_data"
        tmp = td / "tmp"
        url = (src / "qlib_bin.tar.gz").as_uri()

        # 1. 全新安装：manifest 覆盖 + sha 通过 + 更新后复查通过
        build_package(src, ["2026-07-24", "2026-07-27"], ["2026-07-28", "2026-07-29"])
        live._ensure_data_ready(str(provider), "csi300", target, _live_cfg(live, [url], tmp))
        check("live: 全新安装成功", (provider / "calendars" / "day.txt").exists())

        # 2. 数据已最新：不触发下载
        live._ensure_data_ready(str(provider), "csi300", target, _live_cfg(live, ["file:///nonexistent/qlib_bin.tar.gz"], tmp))
        check("live: 已最新跳过更新", True)

        # 3. 上游未就绪（manifest 显示滞后）：下载前硬失败
        shutil.rmtree(provider)
        build_package(src, ["2026-07-23", "2026-07-24"], ["2026-07-27"])
        try:
            live._ensure_data_ready(str(provider), "csi300", target, _live_cfg(live, [url], tmp))
            check("live: 上游滞后硬失败", False, "未抛错")
        except RuntimeError as err:
            check("live: 上游滞后硬失败", "已停止预测/下单" in str(err), str(err))
        check("live: 滞后时未安装数据", not provider.exists())

        # 4. sha 不符：拒绝安装并最终报错
        build_package(src, ["2026-07-24", "2026-07-27"], ["2026-07-28"])
        manifest_path = src / "qlib_bin.manifest.json"
        bad = json.loads(manifest_path.read_text())
        bad["archive_sha256"] = "sha256:" + "0" * 64
        manifest_path.write_text(json.dumps(bad))
        try:
            live._ensure_data_ready(str(provider), "csi300", target, _live_cfg(live, [url], tmp))
            check("live: sha 不符硬失败", False, "未抛错")
        except RuntimeError:
            check("live: sha 不符硬失败", True)

        # 5. 无 manifest 降级：靠解压包日历硬闸拦下过期包
        manifest_path.unlink()
        build_package(src, ["2026-07-23", "2026-07-24"], ["2026-07-27"])
        (src / "qlib_bin.manifest.json").unlink()  # build_package 会重建 manifest
        try:
            live._ensure_data_ready(str(provider), "csi300", target, _live_cfg(live, [url], tmp))
            check("live: 无manifest降级仍拦截过期包", False, "未抛错")
        except RuntimeError:
            check("live: 无manifest降级仍拦截过期包", True)

        # 6. 无 manifest 降级：新鲜包正常安装
        build_package(src, ["2026-07-24", "2026-07-27"], ["2026-07-28"])
        (src / "qlib_bin.manifest.json").unlink()
        live._ensure_data_ready(str(provider), "csi300", target, _live_cfg(live, [url], tmp))
        check("live: 无manifest降级安装新鲜包", (provider / "calendars" / "day.txt").exists())

        # 7. allow_stale_data=True：数据过期仅警告放行
        shutil.rmtree(provider)
        build_package(src, ["2026-07-23", "2026-07-24"], ["2026-07-27"])
        try:
            live._ensure_data_ready(
                str(provider), "csi300", target,
                _live_cfg(live, [url], tmp, allow_stale_data=True),
            )
            check("live: allow_stale_data 放行", True)
        except RuntimeError as err:
            check("live: allow_stale_data 放行", False, str(err))

        # 8. 关闭自动更新 + 数据过期：硬失败（旧行为是仅警告放行）
        try:
            live._ensure_data_ready(
                str(provider), "csi300", target,
                _live_cfg(live, [url], tmp, enable_auto_update=False),
            )
            check("live: 关闭自动更新时过期硬失败", False, "未抛错")
        except RuntimeError:
            check("live: 关闭自动更新时过期硬失败", True)


def test_manual_ensure_data_ready():
    print("== manual_daily_trade._ensure_data_ready 端到端 ==")
    import manual_daily_trade as manual

    trade_date = "2026-07-28"
    required = "2026-07-27"
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = td / "src"
        src.mkdir()
        provider = td / "cn_data"
        tmp = td / "tmp"
        url = (src / "qlib_bin.tar.gz").as_uri()

        def cfg(**kw):
            return manual.DataUpdateConfig(
                enable_auto_update=kw.get("enable_auto_update", True),
                allow_stale_data=kw.get("allow_stale_data", False),
                data_source_url=url,
                data_source_urls=[url],
                retry_count=1,
                retry_interval=0,
                temp_dir=str(tmp),
            )

        # 1. 全新安装 + 返回按新日历复核的 required
        build_package(src, ["2026-07-24", "2026-07-27"], ["2026-07-28", "2026-07-29"])
        out = manual._ensure_data_ready(str(provider), "csi300", trade_date, required, cfg())
        check("manual: 全新安装成功", (provider / "calendars" / "day.txt").exists())
        check("manual: 返回 required 正确", out == "2026-07-27", out)

        # 2. 上游滞后：硬失败且不安装
        shutil.rmtree(provider)
        build_package(src, ["2026-07-23", "2026-07-24"], ["2026-07-27"])
        try:
            manual._ensure_data_ready(str(provider), "csi300", trade_date, required, cfg())
            check("manual: 上游滞后硬失败", False, "未抛错")
        except RuntimeError as err:
            check("manual: 上游滞后硬失败", "blocked" in str(err), str(err))

        # 3. 无 manifest 降级 + 过期包拦截
        (src / "qlib_bin.manifest.json").unlink()
        try:
            manual._ensure_data_ready(str(provider), "csi300", trade_date, required, cfg())
            check("manual: 无manifest降级仍拦截过期包", False, "未抛错")
        except RuntimeError:
            check("manual: 无manifest降级仍拦截过期包", True)

        # 4. allow_stale_data 放行
        out = manual._ensure_data_ready(
            str(provider), "csi300", trade_date, required, cfg(allow_stale_data=True)
        )
        check("manual: allow_stale_data 放行", out == required)


def test_real_manifest_endpoints():
    print("== 真实网络端点 ==")
    urls_official = ["https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz"]
    try:
        manifests = guard.fetch_manifests(urls_official)
        top = guard.best_manifest(manifests)
        check(
            "chenditc manifest 可抓取",
            top is not None and bool(top.get("target_trade_date")) and bool(top.get("archive_sha256")),
            str(top)[:120],
        )
        if top:
            print(f"  [INFO] 官方源 target_trade_date={top['target_trade_date']}, future_start_date={top.get('future_start_date')}")
    except Exception as err:
        check("chenditc manifest 可抓取", False, f"网络异常: {err}")

    urls_fork = ["https://github.com/jzhongsun/investment_data/releases/latest/download/qlib_bin.tar.gz"]
    try:
        manifests = guard.fetch_manifests(urls_fork)
        check("jzhongsun fork 无 manifest 时优雅降级", manifests == {})
    except Exception as err:
        check("jzhongsun fork 无 manifest 时优雅降级", False, f"抛出异常: {err}")


if __name__ == "__main__":
    test_pure_functions()
    test_live_ensure_data_ready()
    test_manual_ensure_data_ready()
    test_real_manifest_endpoints()
    print(f"\n结果: {len(PASSED)} 通过, {len(FAILED)} 失败")
    if FAILED:
        print("失败项:", FAILED)
        sys.exit(1)

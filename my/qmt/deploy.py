"""Create the tiny QMT-editor entry script without copying account data."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, Sequence


ENTRY_TEMPLATE = '''# coding: utf-8
import json
import os
import sys

REPO_ROOT = {repo_root!r}
CONFIG_PATH = {config_path!r}

def _entry_error():
    if sys.version_info < (3, 8):
        return "QMT Python 3.8+ is required; run qmt_probe.py and keep trading disabled"
    if not os.path.isfile(CONFIG_PATH):
        return "QMT config does not exist: " + CONFIG_PATH
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as stream:
            config = json.load(stream)
    except Exception as exc:
        return "QMT config cannot be read: " + repr(exc)
    configured_root = config.get("repo_root") if isinstance(config, dict) else None
    if not configured_root:
        return "QMT config.repo_root is missing"
    expected = os.path.normcase(os.path.abspath(REPO_ROOT))
    actual = os.path.normcase(os.path.abspath(configured_root))
    if actual != expected:
        return "QMT config.repo_root does not match deployed code root"
    return ""

_ENTRY_ERROR = _entry_error()
_impl = None
if not _ENTRY_ERROR:
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    try:
        from my.qmt import qmt_strategy as _impl
        _impl.QMT_CONFIG_PATH = CONFIG_PATH
        _impl.QMT_API_NAMESPACE = globals()
    except Exception as exc:
        _ENTRY_ERROR = "QMT implementation import failed: " + repr(exc)

def init(ContextInfo):
    if _ENTRY_ERROR:
        print("[QMT][DISABLED] " + _ENTRY_ERROR)
        try:
            ContextInfo._qlib_qmt_config = None
        except Exception:
            pass
        return None
    return _impl.init(ContextInfo)

def handlebar(ContextInfo):
    if _ENTRY_ERROR:
        return None
    return _impl.handlebar(ContextInfo)
'''


PROBE_ENTRY_TEMPLATE = '''# coding: utf-8
import sys

REPO_ROOT = {repo_root!r}
REPORT_PATH = {report_path!r}
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

_IMPORT_ERROR = ""
_impl = None
try:
    from my.qmt import qmt_probe as _impl
    _impl.PROBE_OUTPUT_PATH = REPORT_PATH
    _impl.QMT_API_NAMESPACE = globals()
except Exception as exc:
    _IMPORT_ERROR = "QMT read-only probe import failed: " + repr(exc)

def init(ContextInfo):
    if _IMPORT_ERROR:
        print("[qmt-probe][DISABLED] " + _IMPORT_ERROR)
        return None
    return _impl.init(ContextInfo)

def handlebar(ContextInfo):
    if _IMPORT_ERROR:
        return None
    return _impl.handlebar(ContextInfo)
'''


def build_entry(repo_root: Path, output: Path, config_path: Optional[Path] = None) -> Path:
    repo_root = Path(repo_root).resolve()
    config_path = Path(config_path or repo_root / "my" / "runtime" / "qmt_config.json").resolve()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(ENTRY_TEMPLATE.format(repo_root=str(repo_root), config_path=str(config_path)))
    temporary.replace(output)
    return output


def build_probe_entry(repo_root: Path, output: Path, report_path: Optional[Path] = None) -> Path:
    """Generate a QMT-editor wrapper around the repository's read-only probe."""

    repo_root = Path(repo_root).resolve()
    report_path = Path(
        report_path or repo_root / "my" / "runtime" / "qmt_state" / "qmt_probe.json"
    ).resolve()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(PROBE_ENTRY_TEMPLATE.format(repo_root=str(repo_root), report_path=str(report_path)))
    temporary.replace(output)
    return output


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a standard-QMT editor entry script")
    parser.add_argument("--kind", choices=("strategy", "probe"), default="strategy")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--probe-report", type=Path)
    args = parser.parse_args(argv)
    if args.kind == "probe":
        if args.config is not None:
            parser.error("--config is only valid with --kind strategy")
        built = build_probe_entry(args.repo_root, args.output, args.probe_report)
    else:
        if args.probe_report is not None:
            parser.error("--probe-report is only valid with --kind probe")
        built = build_entry(args.repo_root, args.output, args.config)
    print(built)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

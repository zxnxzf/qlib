"""Versioned, tamper-evident file protocol shared by Qlib and standard QMT.

Only Python's standard library is used here.  Runtime JSON files are immutable:
writing the same payload again is an idempotent success, while attempting to
replace an existing path with different content is rejected.
"""

import hashlib
import hmac
import json
import math
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


PLANNER_VERSION = "shared-planner-v1"
SIGNAL_SCHEMA_VERSION = 2
RESULT_SCHEMA_VERSION = 1
EOD_SCHEMA_VERSION = 1

_CHECKSUM_PREFIX = "sha256:"
_CHECKSUM_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_TIMESTAMP_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$"
)
_SHANGHAI_OFFSET = timedelta(hours=8)
_STALE_LOCK_SECONDS = 30.0


class QmtProtocolError(ValueError):
    """Base error for invalid or unsafe QMT protocol data."""


class ProtocolValidationError(QmtProtocolError):
    """A payload does not conform to its protocol schema."""


class ChecksumError(QmtProtocolError):
    """A payload is missing a valid checksum or has been modified."""


class ExpiredSignalError(ProtocolValidationError):
    """A valid signal was read at or after its expiry time."""


class DuplicatePayloadError(QmtProtocolError):
    """An immutable protocol path already contains different content."""


def _default_runtime_root():
    return Path(__file__).resolve().parents[1] / "runtime"


def resolve_runtime_root(runtime_root=None):
    """Return an absolute runtime root, defaulting to ``my/runtime``."""

    root = _default_runtime_root() if runtime_root is None else Path(runtime_root).expanduser()
    return root.resolve()


def _parse_date(value, field_name):
    if isinstance(value, datetime):
        raise ProtocolValidationError("%s must be a YYYY-MM-DD date" % field_name)
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ProtocolValidationError("%s must be a YYYY-MM-DD date" % field_name)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ProtocolValidationError("%s must be a valid YYYY-MM-DD date" % field_name) from exc
    if parsed.isoformat() != value:
        raise ProtocolValidationError("%s must use YYYY-MM-DD format" % field_name)
    return parsed


def _date_text(value, field_name="exec_date"):
    return _parse_date(value, field_name).isoformat()


def _parse_timestamp(value, field_name, require_shanghai=False):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        match = _TIMESTAMP_RE.match(value)
        if match is None:
            raise ProtocolValidationError("%s must be an ISO-8601 timestamp with timezone" % field_name)
        date_part, time_part, fraction, offset_text = match.groups()
        try:
            parsed = datetime.strptime(date_part + "T" + time_part, "%Y-%m-%dT%H:%M:%S")
        except ValueError as exc:
            raise ProtocolValidationError("%s is not a valid timestamp" % field_name) from exc
        if fraction:
            parsed = parsed.replace(microsecond=int(fraction.ljust(6, "0")))
        if offset_text == "Z":
            offset = timedelta(0)
        else:
            sign = 1 if offset_text[0] == "+" else -1
            hours = int(offset_text[1:3])
            minutes = int(offset_text[4:6])
            if hours > 23 or minutes > 59:
                raise ProtocolValidationError("%s has an invalid timezone offset" % field_name)
            offset = sign * timedelta(hours=hours, minutes=minutes)
        parsed = parsed.replace(tzinfo=timezone(offset))
    else:
        raise ProtocolValidationError("%s must be an ISO-8601 timestamp with timezone" % field_name)

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProtocolValidationError("%s must include a timezone" % field_name)
    if require_shanghai and parsed.utcoffset() != _SHANGHAI_OFFSET:
        raise ProtocolValidationError("%s must use the Asia/Shanghai UTC+08:00 offset" % field_name)
    return parsed


def canonical_json_bytes(payload):
    """Serialize JSON deterministically for checksums and equality checks."""

    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolValidationError("payload is not canonical JSON: %s" % exc) from exc
    return text.encode("utf-8")


def compute_checksum(payload):
    """Compute the specified SHA-256 after removing top-level ``checksum``."""

    if not isinstance(payload, dict):
        raise ProtocolValidationError("protocol payload must be a JSON object")
    content = dict(payload)
    content.pop("checksum", None)
    return _CHECKSUM_PREFIX + hashlib.sha256(canonical_json_bytes(content)).hexdigest()


def with_checksum(payload):
    """Return a shallow copy carrying a freshly computed checksum."""

    if not isinstance(payload, dict):
        raise ProtocolValidationError("protocol payload must be a JSON object")
    signed = dict(payload)
    signed.pop("checksum", None)
    signed["checksum"] = compute_checksum(signed)
    return signed


def verify_checksum(payload):
    """Raise :class:`ChecksumError` unless ``payload`` is intact."""

    if not isinstance(payload, dict):
        raise ChecksumError("protocol payload must be a JSON object")
    supplied = payload.get("checksum")
    if not isinstance(supplied, str) or _CHECKSUM_RE.match(supplied) is None:
        raise ChecksumError("missing or malformed SHA-256 checksum")
    expected = compute_checksum(payload)
    if not hmac.compare_digest(supplied, expected):
        raise ChecksumError("payload checksum verification failed")
    return True


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolValidationError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def read_json(path):
    """Read one strict UTF-8 JSON object and reject duplicate keys."""

    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise QmtProtocolError("cannot read protocol file %s: %s" % (path, exc)) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolValidationError("protocol file is not valid UTF-8: %s" % path) from exc
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except ProtocolValidationError:
        raise
    except (TypeError, ValueError) as exc:
        raise ProtocolValidationError("invalid JSON in %s: %s" % (path, exc)) from exc
    if not isinstance(payload, dict):
        raise ProtocolValidationError("protocol file must contain a JSON object: %s" % path)
    return payload


@contextmanager
def _write_lock(path, timeout_seconds=5.0):
    """Use a small cross-platform lock file to serialize immutable writers."""

    lock_path = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + timeout_seconds
    descriptor = None
    token = uuid.uuid4().hex
    while descriptor is None:
        try:
            descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            if _recover_stale_lock(lock_path):
                continue
            if time.monotonic() >= deadline:
                raise QmtProtocolError("timed out waiting for protocol write lock: %s" % lock_path) from exc
            time.sleep(0.01)
    try:
        metadata = json.dumps(
            {"pid": os.getpid(), "created_at": time.time(), "token": token},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        written = os.write(descriptor, metadata)
        if written != len(metadata):
            raise QmtProtocolError("could not persist complete protocol lock metadata: %s" % lock_path)
        os.fsync(descriptor)
        yield
    finally:
        os.close(descriptor)
        _remove_owned_lock(lock_path, token)


def _recover_stale_lock(lock_path):
    """Remove an old lock only when its age proves it is not a normal writer."""

    try:
        age = time.time() - lock_path.stat().st_mtime
    except FileNotFoundError:
        return True
    if age < _STALE_LOCK_SECONDS:
        return False
    owner_pid = _lock_owner_pid(lock_path)
    if owner_pid is not None and _process_is_alive(owner_pid):
        return False
    try:
        lock_path.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        # Windows refuses to unlink a file still held by a live writer.  Treat
        # that as active even when its timestamp is old.
        return False


def _lock_owner_pid(lock_path):
    try:
        text = lock_path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        metadata = json.loads(text)
    except ValueError:
        metadata = None
    if isinstance(metadata, dict):
        value = metadata.get("pid")
    else:
        # Backward compatibility with the first protocol implementation,
        # which stored only the decimal PID in the lock.
        value = text.strip()
    try:
        pid = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return pid if pid > 0 else None


def _process_is_alive(pid):
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            process_query_limited_information = 0x1000
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            # Access denied still proves that a process owns this PID.
            return kernel32.GetLastError() == 5
        except (AttributeError, OSError):
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _remove_owned_lock(lock_path, token):
    """Never remove a successor writer's lock after stale-lock recovery."""

    try:
        metadata = json.loads(lock_path.read_text(encoding="ascii"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, ValueError):
        return
    if not isinstance(metadata, dict) or metadata.get("token") != token:
        return
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def _existing_content_matches(path, payload):
    try:
        existing = read_json(path)
    except QmtProtocolError as exc:
        raise DuplicatePayloadError("existing immutable file is invalid and will not be replaced: %s" % path) from exc
    return canonical_json_bytes(existing) == canonical_json_bytes(payload)


def _fsync_directory(directory):
    try:
        descriptor = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def atomic_write_json(path, payload):
    """Atomically create immutable UTF-8 JSON.

    Returns ``True`` when a new file is published and ``False`` when the exact
    payload already exists.  A different existing payload is never overwritten.
    """

    path = Path(path)
    if not isinstance(payload, dict):
        raise ProtocolValidationError("protocol payload must be a JSON object")
    canonical_json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)

    # A writer may have crashed after publishing the final file but before
    # removing its lock.  Immutable identical content is safe to reuse without
    # waiting for or touching that potentially stale lock.
    if path.exists():
        if _existing_content_matches(path, payload):
            return False
        raise DuplicatePayloadError("refusing to overwrite immutable protocol file: %s" % path)

    with _write_lock(path):
        if path.exists():
            if _existing_content_matches(path, payload):
                return False
            raise DuplicatePayloadError("refusing to overwrite immutable protocol file: %s" % path)

        temp_path = path.with_name(path.name + ".tmp")
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
        try:
            with open(str(temp_path), "wb") as handle:
                handle.write(rendered.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temp_path), str(path))
            _fsync_directory(path.parent)
        except Exception:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            raise
    return True


def signal_path(runtime_root, exec_date):
    return resolve_runtime_root(runtime_root) / "qmt_inbox" / _date_text(exec_date) / "signal.json"


def result_path(runtime_root, exec_date):
    return resolve_runtime_root(runtime_root) / "qmt_outbox" / _date_text(exec_date) / "result.json"


def eod_snapshot_path(runtime_root, exec_date):
    return resolve_runtime_root(runtime_root) / "qmt_outbox" / _date_text(exec_date) / "eod_snapshot.json"


def _require_fields(payload, fields, kind):
    missing = sorted(field for field in fields if field not in payload)
    if missing:
        raise ProtocolValidationError("%s payload is missing fields: %s" % (kind, ", ".join(missing)))


def _require_nonempty_text(payload, field, kind):
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolValidationError("%s.%s must be non-empty text" % (kind, field))
    return value


def _require_schema(payload, expected, kind):
    value = payload.get("schema_version")
    if isinstance(value, bool) or value != expected:
        raise ProtocolValidationError(
            "%s schema_version must be %s (received %r)" % (kind, expected, value)
        )


def _require_container(payload, field, expected_type, kind):
    value = payload.get(field)
    if not isinstance(value, expected_type):
        raise ProtocolValidationError("%s.%s has an invalid type" % (kind, field))
    return value


def _finite_number(value, field_name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProtocolValidationError("%s must be a finite number" % field_name)
    return value


def _integer(value, field_name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolValidationError("%s must be an integer" % field_name)
    if minimum is not None and value < minimum:
        raise ProtocolValidationError("%s must be at least %s" % (field_name, minimum))
    if maximum is not None and value > maximum:
        raise ProtocolValidationError("%s must be at most %s" % (field_name, maximum))
    return value


def _validate_signal_provenance(provenance):
    required = (
        "source_type",
        "strategy_id",
        "release_id",
        "model_sha256",
        "config_sha256",
        "runtime_code_sha256",
        "source_git_commit",
    )
    _require_fields(provenance, required, "signal.provenance")
    if provenance["source_type"] != "published_model":
        raise ProtocolValidationError("signal.provenance.source_type must be published_model")
    for field in ("strategy_id", "release_id"):
        value = provenance[field]
        if not isinstance(value, str) or not value.strip():
            raise ProtocolValidationError("signal.provenance.%s must be non-empty text" % field)
    for field in ("model_sha256", "config_sha256", "runtime_code_sha256"):
        value = provenance[field]
        if not isinstance(value, str) or _SHA256_RE.match(value) is None:
            raise ProtocolValidationError("signal.provenance.%s must be a 64-character SHA-256" % field)
    source_commit = provenance["source_git_commit"]
    if not isinstance(source_commit, str) or _GIT_COMMIT_RE.match(source_commit) is None:
        raise ProtocolValidationError("signal.provenance.source_git_commit must be a full Git commit hash")


def _validate_signal_params(params):
    required = (
        "topk",
        "candidate_limit",
        "n_drop",
        "hold_thresh",
        "risk_degree",
        "lot",
        "open_cost",
        "close_cost",
        "min_cost",
        "max_slippage",
        "wait_seconds",
    )
    _require_fields(params, required, "signal.params")
    topk = _integer(params["topk"], "signal.params.topk", minimum=1)
    candidate_limit = _integer(params["candidate_limit"], "signal.params.candidate_limit")
    if candidate_limit != 100:
        raise ProtocolValidationError("signal.params.candidate_limit must be 100")
    if topk > candidate_limit:
        raise ProtocolValidationError("signal.params.topk cannot exceed candidate_limit")
    _integer(params["n_drop"], "signal.params.n_drop", minimum=0, maximum=topk)
    _integer(params["hold_thresh"], "signal.params.hold_thresh", minimum=0)
    _integer(params["lot"], "signal.params.lot", minimum=1)
    _integer(params["wait_seconds"], "signal.params.wait_seconds", minimum=1, maximum=30)
    risk_degree = _finite_number(params["risk_degree"], "signal.params.risk_degree")
    if not 0 < risk_degree <= 1:
        raise ProtocolValidationError("signal.params.risk_degree must be in (0, 1]")
    for field in ("open_cost", "close_cost", "min_cost"):
        value = _finite_number(params[field], "signal.params.%s" % field)
        if value < 0:
            raise ProtocolValidationError("signal.params.%s cannot be negative" % field)
    slippage = _finite_number(params["max_slippage"], "signal.params.max_slippage")
    if not 0 <= slippage <= 0.003:
        raise ProtocolValidationError("signal.params.max_slippage must be between 0 and 0.003")


def _validate_scores_and_candidates(scores, candidates):
    if not scores:
        raise ProtocolValidationError("signal.scores cannot be empty")
    for code, value in scores.items():
        if not isinstance(code, str) or not code.strip():
            raise ProtocolValidationError("signal.scores keys must be non-empty stock codes")
        _finite_number(value, "signal.scores[%s]" % code)
    expected_codes = sorted(scores, key=lambda code: (-float(scores[code]), code))[:100]
    if len(candidates) != len(expected_codes):
        raise ProtocolValidationError(
            "signal.candidates must contain the complete ranked Top100 (expected %s, received %s)"
            % (len(expected_codes), len(candidates))
        )
    seen_codes = set()
    for expected_rank, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            raise ProtocolValidationError("signal.candidates entries must be objects")
        _require_fields(candidate, ("rank", "code", "score", "reference_close"), "signal.candidates")
        rank = _integer(candidate["rank"], "signal.candidates.rank", minimum=1)
        if rank != expected_rank:
            raise ProtocolValidationError("signal.candidates ranks must be continuous from 1")
        code = candidate["code"]
        if not isinstance(code, str) or not code.strip():
            raise ProtocolValidationError("signal.candidates.code must be a non-empty stock code")
        if code in seen_codes:
            raise ProtocolValidationError("signal.candidates.code must be unique")
        seen_codes.add(code)
        if code not in scores:
            raise ProtocolValidationError("signal candidate %s is missing from scores" % code)
        candidate_score = _finite_number(candidate["score"], "signal.candidates.score")
        if candidate_score != scores[code]:
            raise ProtocolValidationError("signal candidate %s score does not match scores" % code)
        expected_code = expected_codes[expected_rank - 1]
        if code != expected_code:
            raise ProtocolValidationError(
                "signal.candidates must be scores Top100 in score-descending/code-ascending order"
            )
        reference_close = _finite_number(candidate["reference_close"], "signal.candidates.reference_close")
        if reference_close <= 0:
            raise ProtocolValidationError("signal.candidates.reference_close must be positive")


def _validate_result_stage(stage, stage_name):
    list_fields = ("planned", "skipped", "broker_orders", "fills", "cancelled", "errors")
    _require_fields(stage, ("terminal",) + list_fields, "result.%s" % stage_name)
    if not isinstance(stage["terminal"], bool):
        raise ProtocolValidationError("result.%s.terminal must be boolean" % stage_name)
    for field in list_fields:
        if not isinstance(stage[field], list):
            raise ProtocolValidationError("result.%s.%s must be a list" % (stage_name, field))

    planned_by_id = {}
    for index, row in enumerate(stage["planned"]):
        if not isinstance(row, dict):
            raise ProtocolValidationError("result.%s.planned[%d] must be an object" % (stage_name, index))
        order_id = row.get("order_id")
        if not isinstance(order_id, str) or not order_id.strip() or order_id in planned_by_id:
            raise ProtocolValidationError("result.%s planned order ids must be non-empty and unique" % stage_name)
        planned_by_id[order_id] = row

    fill_ids = set()
    filled_by_order = {}
    for field in ("broker_orders", "fills", "cancelled"):
        for index, row in enumerate(stage[field]):
            if not isinstance(row, dict):
                raise ProtocolValidationError(
                    "result.%s.%s[%d] must be an object" % (stage_name, field, index)
                )
            order_id = row.get("order_id")
            planned = planned_by_id.get(order_id) if isinstance(order_id, str) else None
            if planned is not None:
                if row.get("code") not in (None, "") and row["code"] != planned.get("code"):
                    raise ProtocolValidationError(
                        "result.%s.%s fact code does not match its plan" % (stage_name, field)
                    )
                if row.get("side") not in (None, "") and row["side"] != planned.get("side", stage_name):
                    raise ProtocolValidationError(
                        "result.%s.%s fact side does not match its plan" % (stage_name, field)
                    )
            if field != "fills":
                continue
            fill_id = row.get("fill_id")
            if not isinstance(fill_id, str) or not fill_id.strip() or fill_id in fill_ids:
                raise ProtocolValidationError("result.%s fill ids must be non-empty and unique" % stage_name)
            fill_ids.add(fill_id)
            shares = _integer(row.get("shares"), "result.%s.fills.shares" % stage_name, minimum=0)
            price = _finite_number(row.get("price"), "result.%s.fills.price" % stage_name)
            if shares and price <= 0:
                raise ProtocolValidationError("result.%s filled price must be positive" % stage_name)
            if planned is not None:
                filled_by_order[order_id] = filled_by_order.get(order_id, 0) + shares

    for order_id, filled in filled_by_order.items():
        planned_shares = planned_by_id[order_id].get("shares")
        if isinstance(planned_shares, int) and not isinstance(planned_shares, bool) and filled > planned_shares:
            raise ProtocolValidationError(
                "result.%s fills exceed planned shares for %s" % (stage_name, order_id)
            )


def _validate_account_snapshot(account, field_name, allow_empty=False):
    if not account:
        if allow_empty:
            return False
        raise ProtocolValidationError("result.%s must be a complete non-empty account snapshot" % field_name)
    required = ("cash", "market_value", "total_asset", "holdings")
    _require_fields(account, required, "result.%s" % field_name)
    for field in ("cash", "market_value", "total_asset"):
        _finite_number(account[field], "result.%s.%s" % (field_name, field))
    if not isinstance(account["holdings"], list):
        raise ProtocolValidationError("result.%s.holdings must be a list" % field_name)
    return True


def _stage_has_activity(stage):
    return any(stage[field] for field in ("planned", "skipped", "broker_orders", "fills", "cancelled", "errors"))


def validate_signal(
    payload,
    expected_exec_date=None,
    expected_account_alias=None,
    expected_planner_version=None,
    now=None,
    check_expiry=True,
):
    """Validate one signed signal V2 payload and its execution constraints."""

    fields = (
        "schema_version",
        "batch_id",
        "signal_date",
        "exec_date",
        "created_at",
        "expires_at",
        "account_alias",
        "data_asof",
        "provenance",
        "planner_version",
        "gate",
        "params",
        "scores",
        "candidates",
        "checksum",
    )
    _require_fields(payload, fields, "signal")
    _require_schema(payload, SIGNAL_SCHEMA_VERSION, "signal")
    verify_checksum(payload)
    _require_nonempty_text(payload, "batch_id", "signal")
    account_alias = _require_nonempty_text(payload, "account_alias", "signal")
    planner_version = _require_nonempty_text(payload, "planner_version", "signal")
    signal_date_value = _parse_date(payload["signal_date"], "signal.signal_date")
    exec_date_value = _parse_date(payload["exec_date"], "signal.exec_date")
    data_asof_value = _parse_date(payload["data_asof"], "signal.data_asof")
    if data_asof_value != signal_date_value:
        raise ProtocolValidationError("signal.data_asof must equal signal.signal_date")
    if signal_date_value >= exec_date_value:
        raise ProtocolValidationError("signal.signal_date must precede signal.exec_date")
    if expected_exec_date is not None and exec_date_value != _parse_date(expected_exec_date, "expected_exec_date"):
        raise ProtocolValidationError("signal.exec_date does not match the requested execution date")
    if expected_account_alias is not None and account_alias != expected_account_alias:
        raise ProtocolValidationError("signal.account_alias does not match the configured account alias")
    if expected_planner_version is not None and planner_version != expected_planner_version:
        raise ProtocolValidationError("signal.planner_version does not match the running planner")

    created_at = _parse_timestamp(payload["created_at"], "signal.created_at", require_shanghai=True)
    expires_at = _parse_timestamp(payload["expires_at"], "signal.expires_at", require_shanghai=True)
    if expires_at <= created_at:
        raise ProtocolValidationError("signal.expires_at must be later than signal.created_at")
    if expires_at.date() != exec_date_value:
        raise ProtocolValidationError("signal.expires_at must fall on signal.exec_date")
    if check_expiry:
        checked_at = datetime.now(timezone.utc) if now is None else _parse_timestamp(now, "now")
        if checked_at.astimezone(timezone.utc) >= expires_at.astimezone(timezone.utc):
            raise ExpiredSignalError("signal has expired")

    provenance = _require_container(payload, "provenance", dict, "signal")
    _require_container(payload, "gate", dict, "signal")
    if not isinstance(payload["gate"].get("on"), bool):
        raise ProtocolValidationError("signal.gate.on must be boolean")
    params = _require_container(payload, "params", dict, "signal")
    scores = _require_container(payload, "scores", dict, "signal")
    candidates = _require_container(payload, "candidates", list, "signal")
    _validate_signal_provenance(provenance)
    _validate_signal_params(params)
    _validate_scores_and_candidates(scores, candidates)
    return payload


def validate_result(
    payload,
    expected_exec_date=None,
    expected_batch_id=None,
    expected_planner_version=None,
):
    fields = (
        "schema_version",
        "batch_id",
        "signal_date",
        "exec_date",
        "planner_version",
        "started_at",
        "finished_at",
        "status",
        "reason",
        "account_before",
        "market_snapshot",
        "buy_market_snapshot",
        "sell_stage",
        "account_after_sell",
        "buy_stage",
        "account_after",
        "errors",
        "checksum",
    )
    _require_fields(payload, fields, "result")
    _require_schema(payload, RESULT_SCHEMA_VERSION, "result")
    verify_checksum(payload)
    batch_id = _require_nonempty_text(payload, "batch_id", "result")
    planner_version = _require_nonempty_text(payload, "planner_version", "result")
    signal_date_value = _parse_date(payload["signal_date"], "result.signal_date")
    exec_date_value = _parse_date(payload["exec_date"], "result.exec_date")
    if signal_date_value >= exec_date_value:
        raise ProtocolValidationError("result.signal_date must precede result.exec_date")
    if expected_exec_date is not None and exec_date_value != _parse_date(expected_exec_date, "expected_exec_date"):
        raise ProtocolValidationError("result.exec_date does not match the requested execution date")
    if expected_batch_id is not None and batch_id != expected_batch_id:
        raise ProtocolValidationError("result.batch_id does not match the expected batch")
    if expected_planner_version is not None and planner_version != expected_planner_version:
        raise ProtocolValidationError("result.planner_version does not match the running planner")
    started_at = _parse_timestamp(payload["started_at"], "result.started_at")
    finished_at = _parse_timestamp(payload["finished_at"], "result.finished_at")
    if finished_at < started_at:
        raise ProtocolValidationError("result.finished_at cannot precede result.started_at")
    if finished_at.date() != exec_date_value:
        raise ProtocolValidationError("result.finished_at must fall on result.exec_date")
    status = payload["status"]
    if status not in ("completed", "partial", "aborted"):
        raise ProtocolValidationError("result.status must be completed, partial, or aborted")
    if payload["reason"] is not None and not isinstance(payload["reason"], str):
        raise ProtocolValidationError("result.reason must be text or null")
    object_fields = (
        "account_before",
        "market_snapshot",
        "buy_market_snapshot",
        "sell_stage",
        "account_after_sell",
        "buy_stage",
        "account_after",
    )
    for field in object_fields:
        _require_container(payload, field, dict, "result")
    _validate_result_stage(payload["sell_stage"], "sell_stage")
    _validate_result_stage(payload["buy_stage"], "buy_stage")
    _require_container(payload, "errors", list, "result")
    account_fields = ("account_before", "account_after_sell", "account_after")
    if status in ("completed", "partial"):
        if not payload["sell_stage"]["terminal"] or not payload["buy_stage"]["terminal"]:
            raise ProtocolValidationError("completed/partial result stages must be proven terminal")
        if not payload["buy_market_snapshot"]:
            raise ProtocolValidationError("completed/partial result requires buy_market_snapshot")
        for field in account_fields:
            _validate_account_snapshot(payload[field], field)
    else:
        reached = {
            field: _validate_account_snapshot(payload[field], field, allow_empty=True)
            for field in account_fields
        }
        if not reached["account_before"]:
            if reached["account_after_sell"] or reached["account_after"]:
                raise ProtocolValidationError("aborted result cannot contain later accounts before account_before")
            if _stage_has_activity(payload["sell_stage"]) or _stage_has_activity(payload["buy_stage"]):
                raise ProtocolValidationError("aborted result has stage activity before account_before")
        if not reached["account_after_sell"] and _stage_has_activity(payload["buy_stage"]):
            raise ProtocolValidationError("aborted result has buy-stage activity before account_after_sell")
    return payload


def validate_eod_snapshot(payload, expected_exec_date=None, expected_batch_id=None):
    fields = (
        "schema_version",
        "batch_id",
        "exec_date",
        "snapshot_at",
        "cash",
        "frozen_cash",
        "market_value",
        "total_asset",
        "holdings",
        "external_cash_flow",
        "source",
        "checksum",
    )
    _require_fields(payload, fields, "eod_snapshot")
    _require_schema(payload, EOD_SCHEMA_VERSION, "eod_snapshot")
    verify_checksum(payload)
    batch_id = _require_nonempty_text(payload, "batch_id", "eod_snapshot")
    exec_date_value = _parse_date(payload["exec_date"], "eod_snapshot.exec_date")
    if expected_exec_date is not None and exec_date_value != _parse_date(expected_exec_date, "expected_exec_date"):
        raise ProtocolValidationError("eod_snapshot.exec_date does not match the requested execution date")
    if expected_batch_id is not None and batch_id != expected_batch_id:
        raise ProtocolValidationError("eod_snapshot.batch_id does not match the expected batch")
    snapshot_at = _parse_timestamp(
        payload["snapshot_at"], "eod_snapshot.snapshot_at", require_shanghai=True
    )
    if snapshot_at.date() != exec_date_value:
        raise ProtocolValidationError("eod_snapshot.snapshot_at must fall on eod_snapshot.exec_date")
    if snapshot_at.timetz().replace(tzinfo=None) < datetime.strptime("15:00:00", "%H:%M:%S").time():
        raise ProtocolValidationError("eod_snapshot.snapshot_at cannot be before 15:00 Asia/Shanghai")
    if payload["source"] != "broker_qmt":
        raise ProtocolValidationError("eod_snapshot.source must be broker_qmt")
    for field in ("cash", "frozen_cash", "market_value"):
        _finite_number(payload[field], "eod_snapshot.%s" % field)
    total_asset = payload["total_asset"]
    if total_asset is not None:
        _finite_number(total_asset, "eod_snapshot.total_asset")
    external_flow = payload["external_cash_flow"]
    if external_flow is not None:
        _finite_number(external_flow, "eod_snapshot.external_cash_flow")
    _require_container(payload, "holdings", list, "eod_snapshot")
    return payload


def write_signal(runtime_root, payload):
    signed = with_checksum(payload)
    validate_signal(signed, expected_exec_date=signed.get("exec_date"), check_expiry=False)
    path = signal_path(runtime_root, signed["exec_date"])
    atomic_write_json(path, signed)
    return path


def read_signal(
    runtime_root,
    exec_date,
    *,
    expected_account_alias,
    expected_planner_version,
    now=None
):
    payload = read_json(signal_path(runtime_root, exec_date))
    return validate_signal(
        payload,
        expected_exec_date=exec_date,
        expected_account_alias=expected_account_alias,
        expected_planner_version=expected_planner_version,
        now=now,
        check_expiry=True,
    )


def write_result(runtime_root, payload):
    signed = with_checksum(payload)
    validate_result(signed, expected_exec_date=signed.get("exec_date"))
    path = result_path(runtime_root, signed["exec_date"])
    atomic_write_json(path, signed)
    return path


def read_result(
    runtime_root,
    exec_date,
    *,
    expected_batch_id=None,
    expected_planner_version=None
):
    payload = read_json(result_path(runtime_root, exec_date))
    return validate_result(
        payload,
        expected_exec_date=exec_date,
        expected_batch_id=expected_batch_id,
        expected_planner_version=expected_planner_version,
    )


def write_eod_snapshot(runtime_root, payload):
    signed = with_checksum(payload)
    validate_eod_snapshot(signed, expected_exec_date=signed.get("exec_date"))
    path = eod_snapshot_path(runtime_root, signed["exec_date"])
    atomic_write_json(path, signed)
    return path


def read_eod_snapshot(runtime_root, exec_date, *, expected_batch_id=None):
    payload = read_json(eod_snapshot_path(runtime_root, exec_date))
    return validate_eod_snapshot(
        payload,
        expected_exec_date=exec_date,
        expected_batch_id=expected_batch_id,
    )


__all__ = [
    "PLANNER_VERSION",
    "SIGNAL_SCHEMA_VERSION",
    "RESULT_SCHEMA_VERSION",
    "EOD_SCHEMA_VERSION",
    "QmtProtocolError",
    "ProtocolValidationError",
    "ChecksumError",
    "ExpiredSignalError",
    "DuplicatePayloadError",
    "canonical_json_bytes",
    "compute_checksum",
    "with_checksum",
    "verify_checksum",
    "resolve_runtime_root",
    "signal_path",
    "result_path",
    "eod_snapshot_path",
    "read_json",
    "atomic_write_json",
    "validate_signal",
    "validate_result",
    "validate_eod_snapshot",
    "write_signal",
    "read_signal",
    "write_result",
    "read_result",
    "write_eod_snapshot",
    "read_eod_snapshot",
]

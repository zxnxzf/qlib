# -*- coding: utf-8 -*-
"""Read-only capability probe for the QMT embedded Python runtime.

Run this file as a QMT strategy.  ``init`` only initializes local state and
``handlebar`` performs the probe once, and only when ``is_last_bar()`` is true.
The implementation deliberately contains no order or cancel call.

When QMT copies the strategy outside the repository, set
``PROBE_OUTPUT_PATH`` to an absolute path under the Windows qlib checkout.
The source is ASCII-only (apart from this encoding declaration) so it can be
copied through editors configured for either UTF-8 or GBK.
"""

from __future__ import print_function

import inspect
import json
import os
import platform
import sys
from datetime import datetime, timezone


PROBE_VERSION = "qmt-readonly-probe-v1"

# Leave empty when this file stays in ``my/qmt``.  If QMT copies it into its
# own strategy directory, set an absolute path such as:
# r"D:\code\qlib\my\runtime\qmt_state\qmt_probe.json"
PROBE_OUTPUT_PATH = ""

# An empty account id makes the probe use ContextInfo.accid.  The id is never
# written to the report; only its source and a redacted marker are recorded.
PROBE_ACCOUNT_ID = ""
PROBE_ACCOUNT_TYPE = "STOCK"
PROBE_POSITION_STRATEGY_NAME = ""
PROBE_SYMBOLS = ("000001.SZ",)

# Reserved for a later, separately reviewed simulated-order probe.  Even when
# both values below are changed, this version only records the request and
# still does not submit an order.
ENABLE_SIMULATED_ORDER_PROBE = False
DANGEROUS_ORDER_PROBE_ACK = ""
REQUIRED_DANGEROUS_ACK = "I_UNDERSTAND_SIMULATED_ORDER_CAN_TRADE"

MAX_OBJECTS = 3
MAX_FIELDS = 160
MAX_ITEMS = 30
MAX_STRING = 500

_PROBE_DONE = False
QMT_API_NAMESPACE = None


def _utc_timestamp():
    try:
        return datetime.now(timezone.utc).astimezone().isoformat()
    except Exception:
        return datetime.utcnow().isoformat() + "Z"


def _default_output_path():
    if PROBE_OUTPUT_PATH:
        return os.path.abspath(os.path.expanduser(PROBE_OUTPUT_PATH))
    script_path = globals().get("__file__")
    if script_path:
        my_dir = os.path.dirname(os.path.dirname(os.path.abspath(script_path)))
    else:
        my_dir = os.path.join(os.getcwd(), "my")
    return os.path.join(my_dir, "runtime", "qmt_state", "qmt_probe.json")


def _ensure_parent(path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)


def _atomic_write_json(path, payload):
    """Write JSON durably and replace the destination atomically."""

    _ensure_parent(path)
    temp_path = path + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            if os.path.isfile(temp_path):
                os.remove(temp_path)
        except Exception:
            pass
        raise


def _probe_file_write(output_path):
    """Verify create, flush, atomic replace, read, and cleanup beside output."""

    marker_path = output_path + ".write_test"
    temp_path = marker_path + ".tmp"
    marker = "qmt-probe-write-test"
    result = {"status": "error", "atomic_replace": False}
    try:
        _ensure_parent(marker_path)
        with open(temp_path, "w", encoding="ascii") as stream:
            stream.write(marker)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, marker_path)
        with open(marker_path, "r", encoding="ascii") as stream:
            observed = stream.read()
        if observed != marker:
            raise IOError("write verification content mismatch")
        os.remove(marker_path)
        result.update({"status": "ok", "atomic_replace": True})
    except Exception as exc:
        result["error"] = _safe_error(exc)
        for candidate in (temp_path, marker_path):
            try:
                if os.path.isfile(candidate):
                    os.remove(candidate)
            except Exception:
                pass
    return result


def _safe_error(exc):
    # Broker exception messages may echo account ids, symbols, order ids, or
    # raw payloads.  Persist only the exception class in the probe artifact.
    return type(exc).__name__[:MAX_STRING]


def _is_sensitive_field(name):
    compact = str(name or "").lower().replace("_", "")
    markers = (
        "password",
        "passwd",
        "secret",
        "token",
        "accountid",
        "accid",
        "fundaccount",
        "shareholderid",
    )
    return any(marker in compact for marker in markers)


def _safe_value(value, field_name="", depth=0):
    if _is_sensitive_field(field_name) and value not in (None, ""):
        return "<redacted>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_STRING]
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "replace")[:MAX_STRING]
        except Exception:
            return repr(value)[:MAX_STRING]
    if depth >= 3:
        return "<{0}>".format(type(value).__name__)
    if isinstance(value, dict):
        output = {}
        for index, key in enumerate(sorted(value.keys(), key=lambda item: str(item))):
            if index >= MAX_ITEMS:
                output["<truncated>"] = len(value) - MAX_ITEMS
                break
            key_text = str(key)[:MAX_STRING]
            output[key_text] = _safe_value(value[key], key_text, depth + 1)
        return output
    if isinstance(value, (list, tuple, set)):
        values = list(value)
        output = [_safe_value(item, field_name, depth + 1) for item in values[:MAX_ITEMS]]
        if len(values) > MAX_ITEMS:
            output.append("<truncated:{0}>".format(len(values) - MAX_ITEMS))
        return output
    try:
        return repr(value)[:MAX_STRING]
    except Exception:
        return "<{0}:repr-failed>".format(type(value).__name__)


def _value_structure(value, field_name=""):
    """Describe a broker/context field without persisting its business value."""

    if _is_sensitive_field(field_name) and value not in (None, ""):
        return "<redacted>"
    type_name = "{0}.{1}".format(type(value).__module__, type(value).__name__)
    if value is None:
        return {"type": type_name, "is_null": True}
    if isinstance(value, dict):
        return {"type": type_name, "length": len(value)}
    if isinstance(value, (list, tuple, set)):
        item_types = sorted(
            {"{0}.{1}".format(type(item).__module__, type(item).__name__) for item in list(value)[:MAX_ITEMS]}
        )
        return {"type": type_name, "length": len(value), "item_types": item_types}
    if isinstance(value, (str, bytes)):
        return {"type": type_name, "length": len(value)}
    return {"type": type_name}


def _callable_signature(value):
    try:
        signature = inspect.signature(value)
        parameters = []
        for parameter in signature.parameters.values():
            name = parameter.name
            if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
                name = "*" + name
            elif parameter.kind == inspect.Parameter.VAR_KEYWORD:
                name = "**" + name
            parameters.append(name)
        return "({0})".format(", ".join(parameters))[:MAX_STRING]
    except Exception:
        return "<signature-unavailable>"


def _describe_object(value):
    """Return field names and safe values without invoking object methods."""

    result = {
        "type": "{0}.{1}".format(type(value).__module__, type(value).__name__),
        "public_names": [],
        "callable_names": [],
        "fields": {},
        "attribute_errors": {},
    }
    if isinstance(value, dict):
        names = sorted(str(key) for key in value.keys())[:MAX_FIELDS]
        result["public_names"] = names
        for name in names:
            if name in value:
                result["fields"][name] = _value_structure(value[name], name)
        return result

    try:
        names = [name for name in dir(value) if not str(name).startswith("_")]
    except Exception as exc:
        result["attribute_errors"]["dir"] = _safe_error(exc)
        names = []
    names = sorted(str(name) for name in names)[:MAX_FIELDS]
    result["public_names"] = names
    for name in names:
        try:
            item = getattr(value, name)
        except Exception as exc:
            result["attribute_errors"][name] = _safe_error(exc)
            continue
        if callable(item):
            result["callable_names"].append(name)
        else:
            result["fields"][name] = _value_structure(item, name)
    return result


def _records_from_result(raw, mapping_values=False):
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    if isinstance(raw, dict) and mapping_values:
        return list(raw.values())
    if isinstance(raw, dict):
        values = list(raw.values())
        if values and all(isinstance(item, dict) or hasattr(item, "__dict__") for item in values):
            return values
    return [raw]


def _describe_result(raw, mapping_values=False):
    records = _records_from_result(raw, mapping_values=mapping_values)
    return {
        "result_type": "{0}.{1}".format(type(raw).__module__, type(raw).__name__),
        "object_count": len(records),
        "objects": [_describe_object(item) for item in records[:MAX_OBJECTS]],
        "sample_truncated": max(0, len(records) - MAX_OBJECTS),
    }


def _lookup_callable(name, namespace):
    value = namespace.get(name) if namespace is not None else None
    if callable(value):
        return value
    try:
        import builtins

        value = getattr(builtins, name, None)
    except Exception:
        value = None
    return value if callable(value) else None


def _resolve_account_id(context_info):
    configured = str(PROBE_ACCOUNT_ID or "").strip()
    if configured:
        return configured, "PROBE_ACCOUNT_ID"
    for name in ("accid", "account_id", "accountID"):
        try:
            value = getattr(context_info, name, None)
        except Exception:
            value = None
        if value not in (None, ""):
            return str(value).strip(), "ContextInfo.{0}".format(name)
    return "", "unavailable"


def _query_trade_detail(namespace, account_id, kind):
    result = {
        "status": "unavailable",
        "api": "get_trade_detail_data",
        "kind": kind,
        "account_configured": bool(account_id),
        "object_count": 0,
        "objects": [],
        "sample_truncated": 0,
    }
    function = _lookup_callable("get_trade_detail_data", namespace)
    if function is None:
        result["error"] = "get_trade_detail_data is not callable"
        return result, None
    result["signature"] = _callable_signature(function)
    if not account_id:
        result["status"] = "not_configured"
        result["error"] = "account id is unavailable"
        return result, None
    try:
        if kind == "position" and PROBE_POSITION_STRATEGY_NAME:
            raw = function(account_id, PROBE_ACCOUNT_TYPE, kind, PROBE_POSITION_STRATEGY_NAME)
            result["argument_count"] = 4
        else:
            raw = function(account_id, PROBE_ACCOUNT_TYPE, kind)
            result["argument_count"] = 3
        details = _describe_result(raw)
        result.update(details)
        result["status"] = "ok" if details["object_count"] else "empty"
        return result, raw
    except Exception as exc:
        result["status"] = "error"
        result["error"] = _safe_error(exc)
        return result, None


def _find_field(observations, candidates):
    fields = []
    for observation in observations:
        fields.extend(observation.get("fields", {}).keys())
    lower_to_actual = {str(field).lower(): str(field) for field in fields}
    for candidate in candidates:
        actual = lower_to_actual.get(candidate.lower())
        if actual:
            return actual
    return None


ACCOUNT_FIELD_CANDIDATES = {
    "cash": ("m_dAvailable", "available", "cash", "enable_balance"),
    "frozen_cash": ("m_dFrozenCash", "frozen_cash", "frozen"),
    "market_value": ("m_dStockValue", "m_dMarketValue", "market_value", "stock_value"),
    "total_asset": ("m_dTotalAsset", "total_asset"),
}

POSITION_FIELD_CANDIDATES = {
    "code": ("m_strInstrumentID", "instrument_id", "stock_code", "code"),
    "total_volume": ("m_nVolume", "volume", "position", "total_volume"),
    "available_volume": ("m_nCanUseVolume", "can_use_volume", "available", "enable_amount"),
    "cost_price": ("m_dOpenPrice", "cost_price", "open_price"),
    "last_price": ("m_dLastPrice", "last_price", "price"),
    "market_value": ("m_dMarketValue", "market_value", "stock_value"),
}

QUOTE_FIELD_CANDIDATES = {
    "timestamp": ("timetag", "timestamp", "time"),
    "last": ("lastPrice", "last", "price"),
    "bid": ("bidPrice", "bid1", "bid"),
    "ask": ("askPrice", "ask1", "ask"),
    "upper_limit": ("UpStopPrice", "high_limit", "upper_limit"),
    "lower_limit": ("DownStopPrice", "low_limit", "lower_limit"),
}

ORDER_FIELD_CANDIDATES = {
    "order_id": ("m_strOrderSysID", "m_strOrderID", "order_sys_id", "order_id", "entrust_no"),
    "remark": ("m_strRemark", "remark", "user_order_id", "order_remark"),
    "status": ("m_nOrderStatus", "order_status", "status", "entrust_status"),
    "order_volume": ("m_nOrderVolume", "m_nVolume", "order_volume", "volume", "entrust_amount"),
    "traded_volume": (
        "m_nTradedVolume",
        "m_nDealVolume",
        "traded_volume",
        "filled_volume",
        "deal_amount",
    ),
    "order_price": ("m_dOrderPrice", "m_dLimitPrice", "order_price", "price", "entrust_price"),
}

DEAL_FIELD_CANDIDATES = {
    "deal_id": ("m_strTradeID", "m_strDealID", "trade_id", "deal_id", "business_id"),
    "order_id": ("m_strOrderSysID", "m_strOrderID", "order_sys_id", "order_id", "entrust_no"),
    "remark": ("m_strRemark", "remark", "user_order_id", "order_remark"),
    "volume": ("m_nVolume", "m_nDealVolume", "volume", "trade_volume", "filled_volume"),
    "price": ("m_dPrice", "m_dTradePrice", "price", "trade_price", "deal_price"),
    "fee": ("m_dCommission", "m_dFee", "commission", "fee", "trade_fee"),
}


def _matched_fields(observations, candidate_map):
    return {name: _find_field(observations, candidates) for name, candidates in candidate_map.items()}


def _position_codes(raw):
    codes = []
    candidates = POSITION_FIELD_CANDIDATES["code"]
    for item in _records_from_result(raw):
        for field in candidates:
            try:
                value = item.get(field) if isinstance(item, dict) else getattr(item, field, None)
            except Exception:
                value = None
            if value not in (None, ""):
                codes.append(str(value).strip())
                break
    return codes


def _query_quotes(context_info, position_raw):
    result = {"status": "unavailable", "api": "ContextInfo.get_full_tick"}
    try:
        function = getattr(context_info, "get_full_tick", None)
    except Exception as exc:
        result["error"] = _safe_error(exc)
        return result, None
    if not callable(function):
        result["error"] = "ContextInfo.get_full_tick is not callable"
        return result, None
    result["signature"] = _callable_signature(function)
    symbols = []
    for symbol in list(PROBE_SYMBOLS) + _position_codes(position_raw):
        symbol = str(symbol or "").strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    if not symbols:
        result["status"] = "not_configured"
        result["error"] = "no probe symbol or position symbol is available"
        return result, None
    result["requested_symbol_count"] = len(symbols)
    try:
        raw = function(symbols)
        details = _describe_result(raw, mapping_values=True)
        result.update(details)
        result["status"] = "ok" if details["object_count"] else "empty"
        return result, raw
    except Exception as exc:
        result["status"] = "error"
        result["error"] = _safe_error(exc)
        return result, None


def _discover_api_surface(namespace, context_info):
    tokens = ("account", "position", "tick", "order", "deal", "trade", "cancel", "withdraw")
    global_items = {}
    for name, value in sorted((namespace or {}).items(), key=lambda item: str(item[0])):
        name_text = str(name)
        lower = name_text.lower()
        if name_text.startswith("_") or not any(token in lower for token in tokens) or not callable(value):
            continue
        global_items[name_text] = _callable_signature(value)
    context_items = {}
    try:
        names = dir(context_info)
    except Exception:
        names = []
    for name in sorted(str(item) for item in names):
        lower = name.lower()
        if name.startswith("_") or not any(token in lower for token in tokens):
            continue
        try:
            value = getattr(context_info, name)
        except Exception:
            continue
        if callable(value):
            context_items[name] = _callable_signature(value)
    return {"globals": global_items, "context": context_items}


def _fee_fields(*observation_groups):
    tokens = ("fee", "commission", "tax", "transfer")
    fields = set()
    for observations in observation_groups:
        for observation in observations:
            for field in observation.get("fields", {}):
                if any(token in str(field).lower() for token in tokens):
                    fields.add(str(field))
    return sorted(fields)


def _order_probe_status():
    requested = bool(ENABLE_SIMULATED_ORDER_PROBE)
    acknowledged = DANGEROUS_ORDER_PROBE_ACK == REQUIRED_DANGEROUS_ACK
    if not requested:
        status = "disabled"
    elif not acknowledged:
        status = "blocked_missing_ack"
    else:
        status = "not_implemented"
    return {
        "status": status,
        "requested": requested,
        "dangerous_ack_valid": acknowledged,
        "effective": False,
        "order_submitted": False,
        "note": "This probe version never calls an order or cancel API.",
    }


def _runtime_info():
    return {
        "python_version": sys.version,
        "version_info": list(sys.version_info[:5]),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "executable": sys.executable,
        "cwd": os.getcwd(),
        "script_file": globals().get("__file__", "<embedded>"),
        "filesystem_encoding": sys.getfilesystemencoding(),
        "default_encoding": sys.getdefaultencoding(),
    }


def run_probe(context_info, output_path=None, namespace=None, trigger=None):
    """Run all read-only checks and atomically persist one JSON report."""

    if namespace is None:
        namespace = QMT_API_NAMESPACE if QMT_API_NAMESPACE is not None else globals()
    output_path = os.path.abspath(output_path or _default_output_path())
    account_id, account_source = _resolve_account_id(context_info)

    context_observation = _describe_object(context_info)
    context_result = {
        "status": "ok",
        "object": context_observation,
    }
    file_result = _probe_file_write(output_path)
    account_result, account_raw = _query_trade_detail(namespace, account_id, "account")
    position_result, position_raw = _query_trade_detail(namespace, account_id, "position")
    order_result, order_raw = _query_trade_detail(namespace, account_id, "order")
    deal_result, deal_raw = _query_trade_detail(namespace, account_id, "deal")
    quote_result, quote_raw = _query_quotes(context_info, position_raw)

    account_observations = account_result.get("objects", [])
    position_observations = position_result.get("objects", [])
    order_observations = order_result.get("objects", [])
    deal_observations = deal_result.get("objects", [])
    quote_observations = quote_result.get("objects", [])
    account_fields = _matched_fields(account_observations, ACCOUNT_FIELD_CANDIDATES)
    position_fields = _matched_fields(position_observations, POSITION_FIELD_CANDIDATES)
    order_fields = _matched_fields(order_observations, ORDER_FIELD_CANDIDATES)
    deal_fields = _matched_fields(deal_observations, DEAL_FIELD_CANDIDATES)
    quote_fields = _matched_fields(quote_observations, QUOTE_FIELD_CANDIDATES)

    position_empty = position_result.get("status") == "empty"
    position_shape_ready = position_empty or all(
        position_fields.get(name) for name in ("code", "total_volume", "available_volume")
    )
    quote_shape_ready = all(
        quote_fields.get(name)
        for name in ("timestamp", "last", "bid", "ask", "upper_limit", "lower_limit")
    )
    requirements = {
        "context_info": context_result["status"] == "ok",
        "atomic_file_write": file_result.get("status") == "ok",
        "account_query": account_result.get("status") == "ok",
        "account_cash_field": bool(account_fields.get("cash")),
        "position_query": position_result.get("status") in ("ok", "empty"),
        "position_shape": position_shape_ready,
        "quote_query": quote_result.get("status") == "ok",
        "quote_shape": quote_shape_ready,
        "total_asset_field": bool(account_fields.get("total_asset")),
        "market_value_field": bool(
            account_fields.get("market_value") or position_fields.get("market_value")
        ),
    }
    read_only_ready = all(requirements.values())
    order_probe = _order_probe_status()
    report = {
        "schema_version": 1,
        "probe_version": PROBE_VERSION,
        "generated_at": _utc_timestamp(),
        "output_path": output_path,
        "mode": {
            "read_only": True,
            "order_probe": order_probe,
        },
        "trigger": trigger or {"status": "manual", "is_last_bar": None},
        "runtime": _runtime_info(),
        "account": {
            "configured": bool(account_id),
            "source": account_source,
            "value": "<redacted>" if account_id else None,
            "type": PROBE_ACCOUNT_TYPE,
        },
        "capabilities": {
            "context_info": context_result,
            "file_write": file_result,
            "account_query": account_result,
            "position_query": position_result,
            "order_query": order_result,
            "deal_query": deal_result,
            "quote_query": quote_result,
            "discovered_api_surface": _discover_api_surface(namespace, context_info),
        },
        "observed_fields": {
            "account_metrics": account_fields,
            "position": position_fields,
            "position_empty": position_empty,
            "order": order_fields,
            "deal": deal_fields,
            "quote": quote_fields,
            "fee_fields": _fee_fields(
                account_observations,
                position_observations,
                order_observations,
                deal_observations,
                quote_observations,
            ),
        },
        "read_only_requirements": requirements,
        "ready": read_only_ready,
        "ready_for_trading": False,
        "stage_a_complete": False,
        "stage_a_note": (
            "Order submission, callbacks, fee accuracy, cancel, and EOD checks "
            "still require explicit simulation testing."
        ),
    }
    failed = [name for name, passed in requirements.items() if not passed]
    report["failed_requirements"] = failed
    report["errors"] = []
    for name in (
        "file_write",
        "account_query",
        "position_query",
        "order_query",
        "deal_query",
        "quote_query",
    ):
        item = report["capabilities"][name]
        if item.get("status") in ("error", "unavailable", "not_configured"):
            report["errors"].append({"item": name, "error": item.get("error", item.get("status"))})

    _atomic_write_json(output_path, report)
    return report


def _last_bar_status(context_info):
    try:
        function = getattr(context_info, "is_last_bar", None)
    except Exception as exc:
        return False, {"status": "error", "is_last_bar": None, "error": _safe_error(exc)}
    if not callable(function):
        return False, {
            "status": "unavailable",
            "is_last_bar": None,
            "error": "ContextInfo.is_last_bar is not callable",
        }
    try:
        is_last = bool(function())
        return is_last, {"status": "ok", "is_last_bar": is_last}
    except Exception as exc:
        return False, {"status": "error", "is_last_bar": None, "error": _safe_error(exc)}


def _bar_trigger(context_info, last_bar_result):
    trigger = dict(last_bar_result)
    try:
        trigger["barpos"] = _safe_value(getattr(context_info, "barpos", None))
    except Exception as exc:
        trigger["barpos_error"] = _safe_error(exc)
    try:
        function = getattr(context_info, "get_bar_timetag", None)
        if callable(function):
            trigger["bar_timetag"] = _safe_value(function(getattr(context_info, "barpos", None)))
    except Exception as exc:
        trigger["bar_timetag_error"] = _safe_error(exc)
    return trigger


def init(ContextInfo):
    """QMT lifecycle entry: initialize only; never query or trade here."""

    global _PROBE_DONE
    _PROBE_DONE = False
    try:
        ContextInfo._qlib_qmt_probe_done = False
    except Exception:
        pass
    print("[qmt-probe] initialized in strict read-only mode")
    print("[qmt-probe] output: {0}".format(_default_output_path()))


def handlebar(ContextInfo):
    """QMT lifecycle entry: run once and only on the confirmed realtime bar."""

    global _PROBE_DONE
    is_last, last_bar_result = _last_bar_status(ContextInfo)
    if not is_last:
        print("[qmt-probe] skipped: {0}".format(last_bar_result))
        return
    if _PROBE_DONE or bool(getattr(ContextInfo, "_qlib_qmt_probe_done", False)):
        print("[qmt-probe] already completed; skip duplicate realtime bar")
        return
    try:
        report = run_probe(ContextInfo, trigger=_bar_trigger(ContextInfo, last_bar_result))
    except Exception as exc:
        print("[qmt-probe] failed: {0}".format(_safe_error(exc)))
        return
    _PROBE_DONE = True
    try:
        ContextInfo._qlib_qmt_probe_done = True
    except Exception:
        pass
    print("[qmt-probe] report written; ready={0}; failed={1}".format(report["ready"], report["failed_requirements"]))

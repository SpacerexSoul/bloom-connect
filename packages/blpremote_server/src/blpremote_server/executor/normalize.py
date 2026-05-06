"""Response normalisation for Bloomberg messages.

Per M2 IR contract §2.2, normalisation is dispatched on the root
element of each blpapi.Message. Each handler returns a
``NormalizedMessage`` carrying ``data`` (the merged payload), plus
``errors`` and ``warnings`` lists in ``ErrorDetail`` form.

Currently implemented:
  - securityData      → ReferenceData / HistoricalData (incl. per-field
                        error promotion to BLP_FIELD_<CATEGORY>)
  - generic fallback  → element-walk into a plain dict so unknown
                        response types still produce something readable

Stubs for c2 / c3 (this commit ships c1 only):
  - barData    → IntradayBar
  - tickData   → IntradayTick
  - fieldData  → FieldInfo (the response key, NOT the request's `fields`)
  - schema / metaData → SchemaResponse
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from blpremote_server.models import ErrorDetail


@dataclass
class NormalizedMessage:
    """Output of a normaliser. Executor merges these per request."""

    data: dict[str, Any] = field(default_factory=dict)
    errors: list[ErrorDetail] = field(default_factory=list)
    warnings: list[ErrorDetail] = field(default_factory=list)


def normalize_message(message: Any) -> NormalizedMessage:
    """Dispatch on root element of a blpapi.Message."""
    if not hasattr(message, "hasElement"):
        return NormalizedMessage()

    # responseError supersedes everything — surface and stop.
    if message.hasElement("responseError"):
        err = message.getElement("responseError")
        msg_text = (
            err.getElementAsString("message")
            if err.hasElement("message")
            else "Bloomberg responseError"
        )
        return NormalizedMessage(
            errors=[ErrorDetail(code="BLP_RESPONSE_ERROR", message=msg_text)]
        )

    if message.hasElement("securityData"):
        return _normalize_security_data(message)
    if message.hasElement("barData"):
        return _normalize_bar_data(message)  # c2 stub for now
    if message.hasElement("tickData"):
        return _normalize_tick_data(message)  # c2 stub for now
    if message.hasElement("fieldData"):
        return _normalize_field_info(message)  # c3 stub for now
    if message.hasElement("schema") or message.hasElement("metaData"):
        return _normalize_schema(message)  # c3 stub for now

    return _normalize_generic(message)


# ---------- securityData (refdata + historical) ---------------------


def _normalize_security_data(message: Any) -> NormalizedMessage:
    """Refdata (array of securities) or historical (single security)."""
    out = NormalizedMessage()
    sec_data = message.getElement("securityData")
    if sec_data.isArray():
        for i in range(sec_data.numValues()):
            sec_record = sec_data.getValueAsElement(i)
            sec_name = _security_name(sec_record)
            _absorb_security_record(out, sec_record, sec_name, historical=False)
    else:
        sec_name = _security_name(sec_data)
        _absorb_security_record(out, sec_data, sec_name, historical=True)
    return out


def _security_name(sec_element: Any) -> str:
    if sec_element.hasElement("security"):
        return sec_element.getElementAsString("security")
    return "unknown"


def _absorb_security_record(
    out: NormalizedMessage,
    sec_record: Any,
    sec_name: str,
    historical: bool,
) -> None:
    """Promote one securityData record into ``out`` (data + errors)."""
    # Per-security error.
    if sec_record.hasElement("securityError"):
        err = sec_record.getElement("securityError")
        out.errors.append(
            ErrorDetail(
                code="BLP_SECURITY_ERROR",
                message=err.getElementAsString("message"),
                security=sec_name,
            )
        )

    # Per-field exceptions → BLP_FIELD_<CATEGORY>.
    if sec_record.hasElement("fieldExceptions"):
        field_exc = sec_record.getElement("fieldExceptions")
        for j in range(field_exc.numValues()):
            fe = field_exc.getValueAsElement(j)
            field_id = fe.getElementAsString("fieldId")
            err_info = fe.getElement("errorInfo")
            category = (
                err_info.getElementAsString("category")
                if err_info.hasElement("category")
                else "UNKNOWN"
            )
            message_text = (
                err_info.getElementAsString("message")
                if err_info.hasElement("message")
                else ""
            )
            out.errors.append(
                ErrorDetail(
                    code=f"BLP_FIELD_{category}",
                    message=message_text,
                    security=sec_name,
                    field=field_id,
                )
            )

    # Field data.
    if not sec_record.hasElement("fieldData"):
        return
    field_data = sec_record.getElement("fieldData")
    if historical:
        merged = _extract_historical_field_data(field_data)
        if sec_name in out.data:
            for fname, vals in merged.items():
                existing = out.data[sec_name].get(fname)
                if isinstance(existing, list) and isinstance(vals, list):
                    existing.extend(vals)
                else:
                    out.data[sec_name][fname] = vals
        else:
            out.data[sec_name] = merged
    else:
        out.data[sec_name] = _extract_reference_field_data(field_data)


def _extract_reference_field_data(field_data: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for j in range(field_data.numElements()):
        f = field_data.getElement(j)
        name = str(f.name())
        if f.isArray():
            fields[name] = _extract_bulk_data(f)
        else:
            fields[name] = _extract_field_value(f)
    return fields


def _extract_historical_field_data(field_data: Any) -> dict[str, list[Any]]:
    fields_dict: dict[str, list[Any]] = {"dates": []}
    for i in range(field_data.numValues()):
        record = field_data.getValueAsElement(i)
        if record.hasElement("date"):
            fields_dict["dates"].append(record.getElementAsString("date"))
        for j in range(record.numElements()):
            elem = record.getElement(j)
            name = str(elem.name())
            if name == "date":
                continue
            fields_dict.setdefault(name, []).append(_extract_field_value(elem))
    return fields_dict


# ---------- bar / tick / field-info / schema (stubs for c2/c3) ------


def _normalize_bar_data(message: Any) -> NormalizedMessage:
    """IntradayBarResponse — c2 will fill this in."""
    return _normalize_generic(message)


def _normalize_tick_data(message: Any) -> NormalizedMessage:
    """IntradayTickResponse — c2 will fill this in."""
    return _normalize_generic(message)


def _normalize_field_info(message: Any) -> NormalizedMessage:
    """FieldInfoResponse — c3 will fill this in.

    Note: dispatcher keys on the response element ``fieldData`` per
    contract r2 (the request shape uses ``fields`` — easy to confuse).
    """
    return _normalize_generic(message)


def _normalize_schema(message: Any) -> NormalizedMessage:
    """SchemaResponse — c3 will fill this in."""
    return _normalize_generic(message)


def _normalize_generic(message: Any) -> NormalizedMessage:
    """Walk the message into a plain dict — last-resort fallback."""
    out = NormalizedMessage()
    try:
        out.data["_raw"] = _element_to_dict(message)
    except Exception:
        pass
    return out


# ---------- element-level helpers (unchanged behaviour) -------------


def _extract_bulk_data(field: Any) -> list[Any]:
    result: list[Any] = []
    try:
        for i in range(field.numValues()):
            elem = field.getValueAsElement(i)
            if elem.numElements() > 0:
                record: dict[str, Any] = {}
                for j in range(elem.numElements()):
                    sub = elem.getElement(j)
                    record[str(sub.name())] = _extract_field_value(sub)
                result.append(record)
            else:
                result.append(_extract_field_value(elem))
    except Exception:
        pass
    return result


def _extract_field_value(field: Any) -> Any:
    try:
        if field.isNull():
            return None
        if field.isArray():
            return _extract_bulk_data(field)
        dtype = field.datatype()
        if dtype in (1, 2):
            return field.getValueAsBool()
        if dtype in (3, 4, 5, 6, 7, 8):
            return field.getValueAsInteger()
        if dtype in (9, 10):
            return field.getValueAsFloat()
        if dtype == 11:
            return field.getValueAsString()
        if dtype in (12, 13):
            dt = field.getValueAsDatetime()
            if hasattr(dt, "strftime"):
                return dt.strftime("%Y-%m-%d")
            return str(dt)
        return str(field.getValue())
    except Exception:
        try:
            return str(field.getValue())
        except Exception:
            return None


def _element_to_dict(elem: Any) -> Any:
    """Best-effort recursive walk of a blpapi.Element-like object."""
    if not hasattr(elem, "numElements") or not callable(elem.numElements):
        return _extract_field_value(elem)
    if elem.numElements() == 0:
        try:
            if elem.isArray():
                return [
                    _element_to_dict(elem.getValueAsElement(i))
                    for i in range(elem.numValues())
                ]
        except Exception:
            pass
        return _extract_field_value(elem)
    out: dict[str, Any] = {}
    for i in range(elem.numElements()):
        child = elem.getElement(i)
        out[str(child.name())] = _element_to_dict(child)
    return out


# ---------- backwards-compat shims ----------------------------------


def normalize_response(blp_data: dict[str, Any]) -> dict[str, Any]:
    """Legacy shape-cleaner — kept for any callers outside the executor."""
    result: dict[str, Any] = {}
    for security, security_data in blp_data.items():
        if isinstance(security_data, dict):
            result[security] = {k: _scalar(v) for k, v in security_data.items()}
        else:
            result[security] = _scalar(security_data)
    return result


def _scalar(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "value"):
        return _scalar(value.value())
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _scalar(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scalar(v) for v in value]
    return str(value)


def extract_security_data(message: Any) -> dict[str, dict[str, Any]]:
    """Legacy entry point — now wraps normalize_message and returns
    only the data dict (errors are dropped here). New code should call
    ``normalize_message`` for the full NormalizedMessage."""
    return normalize_message(message).data

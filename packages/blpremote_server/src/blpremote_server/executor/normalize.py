"""Response normalization for Bloomberg data."""

from typing import Any


def normalize_response(blp_data: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize Bloomberg response data into a clean dictionary format.

    Converts Bloomberg's nested message structure into a simple
    {security: {field: value}} format.
    """
    result = {}

    for security, security_data in blp_data.items():
        if isinstance(security_data, dict):
            result[security] = {}
            for field, value in security_data.items():
                # Handle special Bloomberg types
                result[security][field] = _normalize_value(value)
        else:
            result[security] = _normalize_value(security_data)

    return result


def _normalize_value(value: Any) -> Any:
    """Normalize a single value from Bloomberg."""
    if value is None:
        return None

    # Handle common Bloomberg special types
    if hasattr(value, "value"):
        # Some Bloomberg types have a .value attribute
        return _normalize_value(value.value())

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_normalize_value(v) for v in value]

    # Convert to string as fallback
    return str(value)


def extract_security_data(message: Any) -> dict[str, dict[str, Any]]:
    """
    Extract security data from a Bloomberg message.

    This is a helper for the executor to parse Bloomberg response messages.
    Returns {security: {field: value}} format.
    """
    result = {}

    # Handle the common ReferenceDataResponse structure
    # securityData is an array of security records
    if hasattr(message, "getElement"):
        try:
            security_data = message.getElement("securityData")
            for i in range(security_data.numValues()):
                security_record = security_data.getValueAsElement(i)
                security_name = security_record.getElementAsString("security")

                field_data = security_record.getElement("fieldData")
                fields = {}

                for j in range(field_data.numElements()):
                    field = field_data.getElement(j)
                    field_name = field.name()
                    fields[str(field_name)] = _extract_field_value(field)

                result[security_name] = fields

        except Exception:
            # If parsing fails, return empty result
            pass

    return result


def _extract_field_value(field: Any) -> Any:
    """Extract the value from a Bloomberg field element."""
    try:
        if field.isNull():
            return None

        # Try different value types
        if field.datatype() in (1, 2):  # BOOL, CHAR
            return field.getValueAsBool()
        elif field.datatype() in (3, 4, 5, 6, 7, 8):  # Integer types
            return field.getValueAsInteger()
        elif field.datatype() in (9, 10):  # Float types
            return field.getValueAsFloat()
        elif field.datatype() == 11:  # STRING
            return field.getValueAsString()
        elif field.datatype() == 12:  # DATETIME
            dt = field.getValueAsDatetime()
            return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None
        else:
            # Default to string
            return str(field.getValue())

    except Exception:
        return None

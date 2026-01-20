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

    Handles both ReferenceDataResponse and HistoricalDataResponse.
    Returns {security: {field: value}} format.
    """
    result = {}

    if not hasattr(message, "getElement"):
        return result

    try:
        # Check message type and dispatch to appropriate handler
        if message.hasElement("securityData"):
            security_data = message.getElement("securityData")

            # Check if this is HistoricalDataResponse (single security)
            # vs ReferenceDataResponse (array of securities)
            if security_data.isArray():
                # ReferenceDataResponse - array of securities
                result = _extract_reference_data(security_data)
            else:
                # HistoricalDataResponse - single security with time series
                result = _extract_historical_data(security_data)

    except Exception as e:
        # If parsing fails, try to at least return the error
        pass

    return result


def _extract_reference_data(security_data: Any) -> dict[str, dict[str, Any]]:
    """Extract data from ReferenceDataResponse (array of securities)."""
    result = {}

    for i in range(security_data.numValues()):
        security_record = security_data.getValueAsElement(i)
        security_name = security_record.getElementAsString("security")

        field_data = security_record.getElement("fieldData")
        fields = {}

        for j in range(field_data.numElements()):
            field = field_data.getElement(j)
            field_name = str(field.name())
            value = _extract_field_value(field)

            # Handle bulk data (arrays like INDX_MEMBERS)
            if field.isArray():
                value = _extract_bulk_data(field)

            fields[field_name] = value

        result[security_name] = fields

    return result


def _extract_historical_data(security_data: Any) -> dict[str, dict[str, Any]]:
    """Extract data from HistoricalDataResponse (time series for single security)."""
    result = {}

    try:
        security_name = security_data.getElementAsString("security")
        field_data = security_data.getElement("fieldData")

        # Historical data has an array of date/value records
        fields_dict: dict[str, list] = {"dates": []}

        for i in range(field_data.numValues()):
            record = field_data.getValueAsElement(i)

            # Get date
            if record.hasElement("date"):
                date_val = record.getElementAsString("date")
                fields_dict["dates"].append(date_val)

            # Get all fields in this record
            for j in range(record.numElements()):
                elem = record.getElement(j)
                field_name = str(elem.name())

                if field_name == "date":
                    continue

                if field_name not in fields_dict:
                    fields_dict[field_name] = []

                fields_dict[field_name].append(_extract_field_value(elem))

        result[security_name] = fields_dict

    except Exception:
        pass

    return result


def _extract_bulk_data(field: Any) -> list[Any]:
    """Extract bulk data (like INDX_MEMBERS array)."""
    result = []

    try:
        for i in range(field.numValues()):
            elem = field.getValueAsElement(i)

            # Bulk data elements can be simple values or sub-records
            if elem.numElements() > 0:
                # Sub-record with multiple fields
                record = {}
                for j in range(elem.numElements()):
                    sub_field = elem.getElement(j)
                    record[str(sub_field.name())] = _extract_field_value(sub_field)
                result.append(record)
            else:
                # Simple value
                result.append(_extract_field_value(elem))

    except Exception:
        pass

    return result


def _extract_field_value(field: Any) -> Any:
    """Extract the value from a Bloomberg field element."""
    try:
        if field.isNull():
            return None

        # Handle arrays (bulk data)
        if field.isArray():
            return _extract_bulk_data(field)

        # Try different value types based on datatype
        dtype = field.datatype()

        if dtype in (1, 2):  # BOOL, CHAR
            return field.getValueAsBool()
        elif dtype in (3, 4, 5, 6, 7, 8):  # Integer types
            return field.getValueAsInteger()
        elif dtype in (9, 10):  # Float types
            return field.getValueAsFloat()
        elif dtype == 11:  # STRING
            return field.getValueAsString()
        elif dtype == 12:  # DATETIME
            dt = field.getValueAsDatetime()
            if hasattr(dt, 'strftime'):
                return dt.strftime("%Y-%m-%d")
            return str(dt)
        elif dtype == 13:  # DATE
            dt = field.getValueAsDatetime()
            if hasattr(dt, 'strftime'):
                return dt.strftime("%Y-%m-%d")
            return str(dt)
        else:
            # Default to string
            return str(field.getValue())

    except Exception:
        # Try to get as string as last resort
        try:
            return str(field.getValue())
        except Exception:
            return None

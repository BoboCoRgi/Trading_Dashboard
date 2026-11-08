import json

_FORBIDDEN_KEYS = {
    "external_account_ref",
    "api_key",
    "apikey",
    "password",
    "secret",
    "token",
    "authorization",
    "raw_payload",
    "raw_payload_json",
    "broker_trade_id",
    "broker_event_id",
}
_DROPPED_KEYS = _FORBIDDEN_KEYS | {
    "account_display_name",
    "account_id",
    "account_number",
    "database_url",
    "id",
}
_FORBIDDEN_TEXT = ("sk-ant-", "bearer ", "mock-t212-isa", "mock-ibkr-isa")


class UnsafeAIContext(ValueError):
    pass


def _reject_text(value: str) -> None:
    lowered = value.lower()
    for needle in _FORBIDDEN_TEXT:
        if needle in lowered:
            raise UnsafeAIContext("AI context contains a forbidden token")


def minimise_context(payload: dict) -> dict:
    """Drop identifiers and replace a long execution list with a count."""

    def walk(value: object, key: str = "") -> object:
        if key.lower() in _DROPPED_KEYS:
            return None
        if isinstance(value, dict):
            cleaned = {}
            for child_key, child in value.items():
                if str(child_key).lower() in _DROPPED_KEYS:
                    continue
                walked = walk(child, str(child_key))
                if walked is not None:
                    cleaned[str(child_key)] = walked
            return cleaned
        if isinstance(value, list):
            if key.lower() in {"trades", "executions", "orders"} and len(value) > 8:
                return {"count": len(value), "note": "Individual executions were omitted."}
            return [item for item in (walk(item, key) for item in value) if item is not None]
        return value

    cleaned = walk(payload)
    if not isinstance(cleaned, dict):
        raise UnsafeAIContext("context must be an object")
    return cleaned


def sanitize_context(payload: dict) -> dict:
    """Return a copy that is safe to send to a model, or raise."""

    def walk(value: object, key: str = "") -> object:
        if key.lower() in _FORBIDDEN_KEYS:
            raise UnsafeAIContext(f"refusing to send '{key}' to a model")
        if isinstance(value, dict):
            return {str(child_key): walk(child, str(child_key)) for child_key, child in value.items()}
        if isinstance(value, list):
            return [walk(item, key) for item in value]
        if isinstance(value, str):
            _reject_text(value)
            return value
        return value

    cleaned = walk(payload)
    if not isinstance(cleaned, dict):
        raise UnsafeAIContext("context must be an object")
    _reject_text(json.dumps(cleaned))
    return cleaned

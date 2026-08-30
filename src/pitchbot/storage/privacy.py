from __future__ import annotations

from pitchbot.domain import JsonValue

DEFAULT_SENSITIVE_KEYS = frozenset(
    {
        "address",
        "contact",
        "contact_number",
        "contact_ref",
        "display_name",
        "email",
        "full_name",
        "mobile",
        "name",
        "phone",
        "phone_number",
        "postal_address",
        "street_address",
        "telephone",
        "whatsapp_number",
    }
)


def redact_json(
    value: JsonValue,
    sensitive_keys: frozenset[str] = DEFAULT_SENSITIVE_KEYS,
) -> JsonValue:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if key.casefold() in sensitive_keys
            else redact_json(child, sensitive_keys)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact_json(child, sensitive_keys) for child in value]
    return value

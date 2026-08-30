from pitchbot.storage.privacy import redact_json


def test_redact_json_handles_nested_sensitive_values() -> None:
    redacted = redact_json(
        {
            "display_name": "Synthetic person",
            "business": {
                "email": "buyer@example.invalid",
                "products": ["shirts", {"whatsapp_number": "+910000000000"}],
                "postal_address": "Synthetic address",
            },
        }
    )

    assert redacted == {
        "display_name": "[REDACTED]",
        "business": {
            "email": "[REDACTED]",
            "products": ["shirts", {"whatsapp_number": "[REDACTED]"}],
            "postal_address": "[REDACTED]",
        },
    }

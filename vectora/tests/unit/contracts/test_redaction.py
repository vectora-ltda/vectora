"""Testes unitários para sanitização e mascaramento de segredos em contratos."""

from devtools.contracts.redaction import redact_payload


def test_redact_sensitive_keys():
    payload = {
        "api_key": "sk-secret123",
        "nested": {
            "token": "ghp_12345",
            "password": "pass",
            "safe": "visible",
        },
    }
    redacted = redact_payload(payload)
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["token"] == "[REDACTED]"
    assert redacted["nested"]["password"] == "[REDACTED]"
    assert redacted["nested"]["safe"] == "visible"


def test_redact_list_items():
    payload = [{"token": "sec1"}, {"safe": 123}]
    redacted = redact_payload(payload)
    assert redacted[0]["token"] == "[REDACTED]"
    assert redacted[1]["safe"] == 123

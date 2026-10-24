"""Testes unitários para sanitização e mascaramento de segredos em contratos."""

from devtools.contracts.redaction import redact_payload, redact_string


def test_redact_sensitive_keys() -> None:
    payload = {
        "api_key": "sk-secret123",
        "nested": {
            "token": "ghp_12345",
            "password": "pass",
            "safe": "visible",
            "credentials": {"nested_user": "admin", "nested_key": "secret"},
            "x-api-key": ["key1", "key2"],
        },
    }
    redacted = redact_payload(payload)
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["token"] == "[REDACTED]"
    assert redacted["nested"]["password"] == "[REDACTED]"
    assert redacted["nested"]["safe"] == "visible"
    assert redacted["nested"]["credentials"] == "[REDACTED]"
    assert redacted["nested"]["x-api-key"] == "[REDACTED]"


def test_redact_list_items() -> None:
    payload = [{"token": "sec1"}, {"safe": 123}]
    redacted = redact_payload(payload)
    assert redacted[0]["token"] == "[REDACTED]"
    assert redacted[1]["safe"] == 123


def test_redact_string_patterns() -> None:
    # sk- pattern
    assert (
        redact_string("Authorization: sk-abc1234567890abcdef1234")
        == "Authorization: [REDACTED_API_KEY]"
    )
    # ghp_ pattern
    assert (
        redact_string("Token: ghp_1234567890abcdef1234567890abcdef")
        == "Token: [REDACTED_GH_TOKEN]"
    )
    # Bearer pattern
    assert (
        redact_string("Header: Bearer abcdef1234567890abcdef1234567890")
        == "Header: Bearer [REDACTED_TOKEN]"
    )
    # JWT pattern
    jwt_sample = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    assert redact_string(f"JWT: {jwt_sample}") == "JWT: [REDACTED_JWT]"


def test_redaction_distinguishes_schema_properties_from_payload() -> None:
    payload = {"type": "x", "description": "d", "password": "secret"}
    assert redact_payload(payload)["password"] == "[REDACTED]"

    schema = {
        "type": "object",
        "properties": {
            "token": {"type": "string", "enum": ["safe-example"]},
            "password": {"enum": ["secret-example"]},
        },
    }
    redacted = redact_payload(schema)
    assert redacted["properties"]["token"]["enum"] == ["safe-example"]
    assert redacted["properties"]["password"]["enum"] == ["secret-example"]


def test_redaction_covers_camel_case_sensitive_keys() -> None:
    redacted = redact_payload({"apiToken": "secret", "session-token": 123})
    assert redacted["apiToken"] == "[REDACTED]"
    assert redacted["session-token"] == "[REDACTED]"


def test_redaction_preserves_paths_and_schema_map_keys() -> None:
    payload = {
        "paths": {"/auth/token": {"post": {"description": "safe"}}},
        "schemas": {"TokenResponse": {"properties": {"token": {"type": "string"}}}},
    }
    redacted = redact_payload(payload)
    assert "/auth/token" in redacted["paths"]
    assert "TokenResponse" in redacted["schemas"]
    assert redacted["schemas"]["TokenResponse"]["properties"]["token"] == {
        "type": "string"
    }


def test_redaction_preserves_map_keys_but_redacts_nested_values() -> None:
    payload = {
        "paths": {
            "/auth/token": {
                "post": {"security": {"api_key": "sk-123456789012345678901234"}}
            }
        },
        "schemas": {
            "TokenResponse": {
                "example": {"access_token": "Bearer abcdefghijklmnopqrstuv"}
            }
        },
    }

    redacted = redact_payload(payload)

    assert "/auth/token" in redacted["paths"]
    assert "TokenResponse" in redacted["schemas"]
    assert (
        redacted["paths"]["/auth/token"]["post"]["security"]["api_key"] == "[REDACTED]"
    )
    assert (
        redacted["schemas"]["TokenResponse"]["example"]["access_token"] == "[REDACTED]"
    )

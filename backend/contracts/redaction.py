"""Módulo de redação e sanitização para exportação de contratos.

Garante que nenhuma credencial, segredo ou valor sensível seja exportado
para os artefatos versionados em contracts/.
"""

from __future__ import annotations

import re
from typing import Any

# Padrões conhecidos de segredos
_SECRET_PATTERNS = [
    (re.compile(r"sk-[a-zA-Z0-9_\-]{20,}", re.IGNORECASE), "[REDACTED_API_KEY]"),
    (re.compile(r"gh[pousr]_[a-zA-Z0-9]{20,}", re.IGNORECASE), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"vst_[a-zA-Z0-9_\-]{20,}", re.IGNORECASE), "[REDACTED_SERVICE_TOKEN]"),
    (
        re.compile(
            r"eyJ[a-zA-Z0-9_\-]{20,}\.[a-zA-Z0-9_\-]{20,}\.[a-zA-Z0-9_\-]{20,}",
            re.IGNORECASE,
        ),
        "[REDACTED_JWT]",
    ),
    (
        re.compile(r"bearer\s+[a-zA-Z0-9_\-\.]{20,}", re.IGNORECASE),
        "Bearer [REDACTED_TOKEN]",
    ),
]

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "api-key",
    "x-api-key",
    "x_api_key",
    "token",
    "id_token",
    "id-token",
    "secret",
    "client_secret",
    "client-secret",
    "password",
    "passwd",
    "credentials",
    "authorization",
    "access_token",
    "access-token",
    "refresh_token",
    "refresh-token",
    "private_key",
    "private-key",
    "api_token",
    "auth_token",
    "secret_key",
    "privatekey",
    "session_token",
    "cookie",
    "set_cookie",
}


def _is_sensitive_key(key: str) -> bool:
    """Executa a operação is sensitive key."""
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    exact = {re.sub(r"[^a-z0-9]", "", item.lower()) for item in _SENSITIVE_KEYS}
    if normalized in exact:
        return True
    # Prefixes and suffixes are common in wire payloads (for example
    # ``oauth_client_secret`` and ``x_api_key``). Keep ``key`` out of this
    # list because generic identifiers such as ``monkey`` are not secrets.
    return any(
        fragment in normalized
        for fragment in (
            "apikey",
            "token",
            "secret",
            "password",
            "passwd",
            "credential",
            "authorization",
            "privatekey",
            "cookie",
        )
    )


def redact_string(value: str) -> str:
    """Redige strings que combinam com padrões de segredo."""
    redacted = value
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_payload(
    obj: Any,
    *,
    _schema_context: bool = False,
    _preserve_map_keys: bool = False,
) -> Any:
    """Aplica redação recursivamente em dicionários, listas e primitivos.

    Preserva estruturas de JSON Schema contidas sob `properties` para não
    descaracterizar schemas de API ao exportar contratos.
    """
    if isinstance(obj, dict):
        new_dict = {}
        for k, v in obj.items():
            key_str = str(k)
            if (
                _is_sensitive_key(key_str)
                and not _schema_context
                and not _preserve_map_keys
            ):
                new_dict[k] = "[REDACTED]"
            else:
                child_schema = _schema_context or key_str in {
                    "properties",
                    "$defs",
                    "definitions",
                }
                new_dict[k] = redact_payload(
                    v,
                    _schema_context=child_schema,
                    _preserve_map_keys=key_str in {"paths", "schemas"},
                )
        return new_dict
    if isinstance(obj, list):
        return [redact_payload(item, _schema_context=_schema_context) for item in obj]
    if isinstance(obj, str):
        return redact_string(obj)
    return obj

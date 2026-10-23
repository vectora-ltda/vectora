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
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower()
    normalized = normalized.replace("-", "_")
    return normalized in {item.replace("-", "_") for item in _SENSITIVE_KEYS}


def redact_string(value: str) -> str:
    """Redige strings que combinam com padrões de segredo."""
    redacted = value
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_payload(obj: Any, *, _schema_context: bool = False) -> Any:
    """Aplica redação recursivamente em dicionários, listas e primitivos.

    Preserva estruturas de JSON Schema contidas sob `properties` para não
    descaracterizar schemas de API ao exportar contratos.
    """
    if isinstance(obj, dict):
        new_dict = {}
        for k, v in obj.items():
            key_str = str(k)
            if _is_sensitive_key(key_str) and not _schema_context:
                new_dict[k] = "[REDACTED]"
            else:
                child_schema = _schema_context or key_str in {
                    "properties",
                    "$defs",
                    "definitions",
                }
                new_dict[k] = redact_payload(v, _schema_context=child_schema)
        return new_dict
    if isinstance(obj, list):
        return [redact_payload(item, _schema_context=_schema_context) for item in obj]
    if isinstance(obj, str):
        return redact_string(obj)
    return obj

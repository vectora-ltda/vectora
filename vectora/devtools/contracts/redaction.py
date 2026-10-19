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
}


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in _SENSITIVE_KEYS or key.lower() in _SENSITIVE_KEYS


def redact_string(value: str) -> str:
    """Redige strings que combinam com padrões de segredo."""
    redacted = value
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_payload(obj: Any) -> Any:
    """Aplica redação recursivamente em dicionários, listas e primitivos.

    Preserva estruturas de JSON Schema contidas sob `properties` para não
    descaracterizar schemas de API ao exportar contratos.
    """
    if isinstance(obj, dict):
        # Se for um schema (contém `properties` ou `type` com definições de schema),
        # precisamos garantir que não redigimos definições de propriedades estruturais
        new_dict = {}
        is_schema_properties = "type" in obj and (
            "title" in obj or "description" in obj
        )
        for k, v in obj.items():
            key_str = str(k)
            if _is_sensitive_key(key_str) and not is_schema_properties:
                # Redige se não for um schema de tipo (ex: {"type": "string"})
                if isinstance(v, dict) and "type" in v:
                    new_dict[k] = redact_payload(v)
                else:
                    new_dict[k] = "[REDACTED]"
            else:
                new_dict[k] = redact_payload(v)
        return new_dict
    if isinstance(obj, list):
        return [redact_payload(item) for item in obj]
    if isinstance(obj, str):
        return redact_string(obj)
    return obj

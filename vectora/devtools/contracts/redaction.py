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
    "token",
    "secret",
    "password",
    "authorization",
    "client_secret",
    "access_token",
    "refresh_token",
    "private_key",
}


def redact_string(value: str) -> str:
    """Redige strings que combinam com padrões de segredo."""
    redacted = value
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_payload(obj: Any) -> Any:
    """Aplica redação recursivamente em dicionários, listas e primitivos."""
    if isinstance(obj, dict):
        new_dict = {}
        for k, v in obj.items():
            if str(k).lower() in _SENSITIVE_KEYS and isinstance(v, str):
                new_dict[k] = "[REDACTED]"
            else:
                new_dict[k] = redact_payload(v)
        return new_dict
    if isinstance(obj, list):
        return [redact_payload(item) for item in obj]
    if isinstance(obj, str):
        return redact_string(obj)
    return obj

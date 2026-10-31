"""Normalização determinística para comparação e serialização de contratos."""

from __future__ import annotations

import json
from typing import Any


def normalize_json(obj: Any) -> Any:
    """Normaliza recursivamente estruturas ordenando chaves de dicionários."""
    if isinstance(obj, dict):
        return {k: normalize_json(obj[k]) for k in sorted(obj.keys())}
    if isinstance(obj, list):
        return [normalize_json(item) for item in obj]
    return obj


def dumps_normalized(obj: Any) -> str:
    """Serializa objeto JSON com ordenação de chaves e indentação consistente."""
    normalized = normalize_json(obj)
    return json.dumps(normalized, indent=2, ensure_ascii=False) + "\n"

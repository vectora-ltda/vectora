"""Verificação de compatibilidade semântica de schemas e contratos.

Distingue mudanças compatíveis (non-breaking) de mudanças incompatíveis (breaking).
"""

from __future__ import annotations

from typing import Any


class CompatibilityReport:
    def __init__(self) -> None:
        self.breaking: list[str] = []
        self.non_breaking: list[str] = []

    @property
    def is_compatible(self) -> bool:
        return len(self.breaking) == 0


def check_schema_compatibility(
    old_schema: dict[str, Any], new_schema: dict[str, Any], path: str = ""
) -> CompatibilityReport:
    """Verifica compatibilidade entre dois schemas JSON (OpenAPI / JSON Schema)."""
    report = CompatibilityReport()

    # 1. Checa tipos
    old_type = old_schema.get("type")
    new_type = new_schema.get("type")
    if old_type and new_type and old_type != new_type:
        report.breaking.append(
            f"{path}: tipo alterado de '{old_type}' para '{new_type}'"
        )
        return report

    # 2. Checa campos obrigatórios (required)
    old_req = set(old_schema.get("required", []))
    new_req = set(new_schema.get("required", []))
    added_req = new_req - old_req
    if added_req:
        report.breaking.append(
            f"{path}: novos campos obrigatórios adicionados: {sorted(added_req)}"
        )
    removed_req = old_req - new_req
    if removed_req:
        report.non_breaking.append(
            f"{path}: campos não mais obrigatórios: {sorted(removed_req)}"
        )

    # 3. Checa propriedades de objetos
    old_props = old_schema.get("properties", {})
    new_props = new_schema.get("properties", {})
    removed_props = set(old_props.keys()) - set(new_props.keys())
    if removed_props:
        report.breaking.append(f"{path}: campos removidos: {sorted(removed_props)}")
    added_props = set(new_props.keys()) - set(old_props.keys())
    if added_props:
        report.non_breaking.append(
            f"{path}: novos campos opcionais adicionados: {sorted(added_props)}"
        )

    for prop in set(old_props.keys()) & set(new_props.keys()):
        sub_report = check_schema_compatibility(
            old_props[prop], new_props[prop], path=f"{path}.{prop}" if path else prop
        )
        report.breaking.extend(sub_report.breaking)
        report.non_breaking.extend(sub_report.non_breaking)

    return report

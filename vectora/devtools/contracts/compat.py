"""Verificação de compatibilidade semântica de schemas e contratos.

Distingue mudanças compatíveis (non-breaking) de mudanças incompatíveis (breaking).
"""

from __future__ import annotations

from typing import Any


class CompatibilityReport:
    def __init__(self) -> None:
        """Inicializa o estado da instância."""
        self.breaking: list[str] = []
        self.non_breaking: list[str] = []

    @property
    def is_compatible(self) -> bool:
        """Executa a operação is compatible."""
        return len(self.breaking) == 0


# Alias de tipo para JSON Schema arbitrário com justificativa explícita:
# Um JSON Schema representa árvores recursivas de definições com valores primitivos,
# listas e sub-schemas arbitrários, logo `object` / `Any` é exigido para nós genéricos.
type JsonSchemaDict = dict[str, Any]


def check_schema_compatibility(
    old_schema: JsonSchemaDict,
    new_schema: JsonSchemaDict,
    path: str = "",
    direction: str = "request",
) -> CompatibilityReport:
    """Verifica compatibilidade semântica entre dois schemas JSON (OpenAPI / JSON Schema).

    direction: "request" (default) ou "response".
    - Em "request": adicionar campo obrigatório quebra (breaking), remover campo quebra (breaking).
    - Em "response": adicionar campo obrigatório é seguro (non_breaking), remover campo quebra (breaking).
    """
    report = CompatibilityReport()

    # 1. Checa tipos
    old_type = old_schema.get("type")
    new_type = new_schema.get("type")
    if old_type and new_type and old_type != new_type:
        report.breaking.append(
            f"{path}: tipo alterado de '{old_type}' para '{new_type}'"
        )
        return report

    # 2. Checa $ref
    old_ref = old_schema.get("$ref")
    new_ref = new_schema.get("$ref")
    if old_ref and new_ref and old_ref != new_ref:
        report.breaking.append(
            f"{path}: referência $ref alterada de '{old_ref}' para '{new_ref}'"
        )
        return report

    # 3. Checa enum
    old_enum = old_schema.get("enum")
    new_enum = new_schema.get("enum")
    if old_enum is not None and new_enum is not None:
        old_enum_set = set(old_enum)
        new_enum_set = set(new_enum)
        if direction == "request":
            # Em request, reduzir enum quebra clientes
            removed_enums = old_enum_set - new_enum_set
            if removed_enums:
                report.breaking.append(
                    f"{path}: valores de enum removidos em request: {sorted(removed_enums)}"
                )
        else:
            # Consumidores de response podem rejeitar valores que não conhecem.
            added_enums = new_enum_set - old_enum_set
            if added_enums:
                report.breaking.append(
                    f"{path}: novos valores de enum adicionados em response: {sorted(added_enums)}"
                )

    # 4. Checa campos obrigatórios (required)
    old_req = set(old_schema.get("required", []))
    new_req = set(new_schema.get("required", []))
    added_req = new_req - old_req
    if added_req:
        if direction == "request":
            report.breaking.append(
                f"{path}: novos campos obrigatórios adicionados: {sorted(added_req)}"
            )
        else:
            report.non_breaking.append(
                f"{path}: novos campos obrigatórios em response: {sorted(added_req)}"
            )
    removed_req = old_req - new_req
    if removed_req:
        if direction == "response":
            report.breaking.append(
                f"{path}: campos não mais obrigatórios em response: {sorted(removed_req)}"
            )
        else:
            report.non_breaking.append(
                f"{path}: campos não mais obrigatórios: {sorted(removed_req)}"
            )

    # 5. Checa propriedades de objetos
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
            old_props[prop],
            new_props[prop],
            path=f"{path}.{prop}" if path else prop,
            direction=direction,
        )
        report.breaking.extend(sub_report.breaking)
        report.non_breaking.extend(sub_report.non_breaking)

    # 6. Checa items em arrays
    old_items = old_schema.get("items")
    new_items = new_schema.get("items")
    if isinstance(old_items, dict) and isinstance(new_items, dict):
        sub_report = check_schema_compatibility(
            old_items,
            new_items,
            path=f"{path}[]" if path else "[]",
            direction=direction,
        )
        report.breaking.extend(sub_report.breaking)
        report.non_breaking.extend(sub_report.non_breaking)

    # 7. Checa $defs ou definitions
    for defs_key in ("$defs", "definitions"):
        old_defs = old_schema.get(defs_key, {})
        new_defs = new_schema.get(defs_key, {})
        for d_key in set(old_defs.keys()) & set(new_defs.keys()):
            sub_report = check_schema_compatibility(
                old_defs[d_key],
                new_defs[d_key],
                path=f"{path}#{defs_key}/{d_key}" if path else f"#{defs_key}/{d_key}",
                direction=direction,
            )
            report.breaking.extend(sub_report.breaking)
            report.non_breaking.extend(sub_report.non_breaking)

    # 8. Checa oneOf / anyOf / allOf. Alternativas de anyOf/oneOf são conjuntos
    # sem ordem semântica; allOf mantém a ordem porque cada posição é uma
    # restrição correspondente.
    for combo_key in ("oneOf", "anyOf", "allOf"):
        old_combos = old_schema.get(combo_key, [])
        new_combos = new_schema.get(combo_key, [])
        if not isinstance(old_combos, list) or not isinstance(new_combos, list):
            continue
        if combo_key in {"anyOf", "oneOf"}:
            source, target = (
                (old_combos, new_combos)
                if direction == "request"
                else (new_combos, old_combos)
            )
            for index, source_alt in enumerate(source):
                if not isinstance(source_alt, dict):
                    continue
                matched = False
                for target_alt in target:
                    if not isinstance(target_alt, dict):
                        continue
                    candidate = check_schema_compatibility(
                        source_alt,
                        target_alt,
                        path=f"{path}.{combo_key}[{index}]"
                        if path
                        else f"{combo_key}[{index}]",
                        direction=direction,
                    )
                    if candidate.is_compatible:
                        matched = True
                        break
                if not matched:
                    report.breaking.append(
                        f"{path}: alternativa {index} de {combo_key} não possui correspondente"
                    )
            continue

        old_len, new_len = len(old_combos), len(new_combos)
        if old_len != new_len:
            breaking = (direction == "request" and new_len > old_len) or (
                direction == "response" and new_len < old_len
            )
            message = (
                f"{path}: número de alternativas em {combo_key} alterado de "
                f"{old_len} para {new_len}"
            )
            (report.breaking if breaking else report.non_breaking).append(message)
        for index, (old_alt, new_alt) in enumerate(
            zip(old_combos, new_combos, strict=False)
        ):
            if isinstance(old_alt, dict) and isinstance(new_alt, dict):
                sub_report = check_schema_compatibility(
                    old_alt,
                    new_alt,
                    path=f"{path}.{combo_key}[{index}]"
                    if path
                    else f"{combo_key}[{index}]",
                    direction=direction,
                )
                report.breaking.extend(sub_report.breaking)
                report.non_breaking.extend(sub_report.non_breaking)

    return report

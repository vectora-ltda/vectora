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

    # Restrições de cardinalidade e tamanho também fazem parte da semântica do
    # contrato. Em requests, estreitar o domínio rejeita clientes existentes;
    # em responses, alargar o domínio pode emitir valores que consumidores
    # antigos não sabem validar.
    _bounded_constraints = (
        "minLength",
        "minItems",
        "minimum",
        "exclusiveMinimum",
        "maxLength",
        "maxItems",
        "maximum",
        "exclusiveMaximum",
    )
    for constraint in _bounded_constraints:
        old_value = old_schema.get(constraint)
        new_value = new_schema.get(constraint)
        if not isinstance(old_value, (int, float)) or not isinstance(
            new_value, (int, float)
        ):
            continue
        if constraint.startswith("min") or constraint in {
            "minimum",
            "exclusiveMinimum",
        }:
            tightened = new_value > old_value
        else:
            tightened = new_value < old_value
        loosened = new_value != old_value and not tightened
        is_breaking = tightened if direction == "request" else loosened
        message = (
            f"{path}: restrição {constraint} alterada de {old_value!r} para "
            f"{new_value!r}"
        )
        (report.breaking if is_breaking else report.non_breaking).append(message)

    old_additional = old_schema.get("additionalProperties")
    new_additional = new_schema.get("additionalProperties")
    if isinstance(old_additional, bool) and isinstance(new_additional, bool):
        if old_additional != new_additional:
            # Requests are sets of values accepted by the server; responses are
            # sets emitted by the server and consumed by the client.
            tightened = old_additional and not new_additional
            is_breaking = tightened if direction == "request" else not tightened
            message = (
                f"{path}: additionalProperties alterado de {old_additional!r} "
                f"para {new_additional!r}"
            )
            (report.breaking if is_breaking else report.non_breaking).append(message)

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
        if not isinstance(old_defs, dict) or not isinstance(new_defs, dict):
            continue
        removed_defs = set(old_defs) - set(new_defs)
        if removed_defs:
            report.breaking.append(
                f"{path}: definições removidas em {defs_key}: {sorted(removed_defs)}"
            )
        for d_key in set(old_defs.keys()) & set(new_defs.keys()):
            sub_report = check_schema_compatibility(
                old_defs[d_key],
                new_defs[d_key],
                path=f"{path}#{defs_key}/{d_key}" if path else f"#{defs_key}/{d_key}",
                direction=direction,
            )
            report.breaking.extend(sub_report.breaking)
            report.non_breaking.extend(sub_report.non_breaking)

    # 8. Checa oneOf / anyOf / allOf. Todas as combinações são conjuntos de
    # restrições/alternativas: a ordem no JSON não altera a semântica do
    # schema. O pareamento precisa, portanto, ser feito por conteúdo e não por
    # índice (em especial para ``allOf``, que antes gerava falso positivo ao
    # apenas reordenar duas restrições).
    for combo_key in ("oneOf", "anyOf", "allOf"):
        old_combos = old_schema.get(combo_key, [])
        new_combos = new_schema.get(combo_key, [])
        if not isinstance(old_combos, list) or not isinstance(new_combos, list):
            continue
        if combo_key in {"anyOf", "oneOf"}:
            if combo_key == "oneOf" and len(new_combos) > len(old_combos):
                # A new oneOf branch can overlap an existing branch and make
                # previously valid values ambiguous.  Without a full JSON
                # Schema satisfiability solver, treat expansion as breaking
                # in both directions rather than claiming a false proof.
                report.breaking.append(
                    f"{path}: alternativas adicionadas em oneOf podem tornar "
                    "valores ambíguos"
                )
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
                    # For responses the new producer alternative is the
                    # source and the old consumer alternative is the target,
                    # but the recursive checker still receives schemas in
                    # old,new order so enum/required direction is preserved.
                    candidate = check_schema_compatibility(
                        target_alt if direction == "response" else source_alt,
                        source_alt if direction == "response" else target_alt,
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
        matched_target_indexes: set[int] = set()
        # Reserve every exact pair before compatibility matching.  Otherwise
        # an earlier broad schema can consume an exact target belonging to a
        # later alternative and produce a false breaking change.
        exact_matches: dict[int, int] = {}
        for source_index, old_alt in enumerate(old_combos):
            if not isinstance(old_alt, dict):
                continue
            for target_index, new_alt in enumerate(new_combos):
                if target_index not in matched_target_indexes and old_alt == new_alt:
                    exact_matches[source_index] = target_index
                    matched_target_indexes.add(target_index)
                    break
        for source_index, old_alt in enumerate(old_combos):
            if not isinstance(old_alt, dict):
                continue
            match_index: int | None = exact_matches.get(source_index)
            best_report: CompatibilityReport | None = (
                CompatibilityReport() if match_index is not None else None
            )
            # Primeiro consuma uma correspondência estrutural exata. Isso
            # evita que duas restrições independentes de ``allOf`` sejam
            # pareadas cruzadamente apenas porque ambas são individualmente
            # compatíveis quando a lista foi reordenada.
            for target_index, new_alt in enumerate(new_combos):
                if match_index is not None or target_index in matched_target_indexes:
                    continue
                if isinstance(new_alt, dict) and old_alt == new_alt:
                    match_index = target_index
                    best_report = CompatibilityReport()
                    break
            for target_index, new_alt in enumerate(new_combos):
                if (
                    match_index is not None
                    or target_index in matched_target_indexes
                    or not isinstance(new_alt, dict)
                ):
                    continue
                candidate = check_schema_compatibility(
                    old_alt,
                    new_alt,
                    path=f"{path}.{combo_key}[{source_index}]"
                    if path
                    else f"{combo_key}[{source_index}]",
                    direction=direction,
                )
                if best_report is None or len(candidate.breaking) < len(
                    best_report.breaking
                ):
                    best_report = candidate
                if candidate.is_compatible:
                    match_index = target_index
                    best_report = candidate
                    break
            if match_index is None:
                if best_report is not None:
                    report.breaking.extend(best_report.breaking)
                report.breaking.append(
                    f"{path}: alternativa {source_index} de {combo_key} não possui correspondente"
                )
                continue
            matched_target_indexes.add(match_index)
            if best_report is not None:
                report.non_breaking.extend(best_report.non_breaking)

    return report

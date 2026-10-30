"""Testes unitários para verificação de compatibilidade semântica de schemas."""

import pytest

from devtools.contracts.compat import check_schema_compatibility


def test_schema_compat_identical() -> None:
    """Verifica o comportamento de schema compat identical."""
    s1 = {"type": "object", "properties": {"a": {"type": "string"}}}
    s2 = {"type": "object", "properties": {"a": {"type": "string"}}}
    report = check_schema_compatibility(s1, s2)
    assert report.is_compatible
    assert len(report.breaking) == 0
    assert len(report.non_breaking) == 0


def test_schema_compat_added_field() -> None:
    """Verifica o comportamento de schema compat added field."""
    old = {"type": "object", "properties": {"a": {"type": "string"}}}
    new = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
    }
    report = check_schema_compatibility(old, new)
    assert report.is_compatible
    assert len(report.breaking) == 0
    assert len(report.non_breaking) == 1
    assert "b" in report.non_breaking[0]


def test_schema_compat_removed_field() -> None:
    """Verifica o comportamento de schema compat removed field."""
    old = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
    }
    new = {"type": "object", "properties": {"a": {"type": "string"}}}
    report = check_schema_compatibility(old, new)
    assert not report.is_compatible
    assert len(report.breaking) == 1
    assert "b" in report.breaking[0]


def test_schema_compat_type_changed() -> None:
    """Verifica o comportamento de schema compat type changed."""
    old = {"type": "object", "properties": {"a": {"type": "string"}}}
    new = {"type": "object", "properties": {"a": {"type": "integer"}}}
    report = check_schema_compatibility(old, new)
    assert not report.is_compatible
    assert len(report.breaking) == 1
    assert "tipo alterado" in report.breaking[0]


def test_schema_compat_required_fields_by_direction() -> None:
    """Verifica o comportamento de schema compat required fields by direction."""
    old = {"type": "object", "properties": {"a": {"type": "string"}}}
    new = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
        "required": ["b"],
    }
    # Em request: adicionar campo obrigatório quebra compatibilidade
    req_report = check_schema_compatibility(old, new, direction="request")
    assert not req_report.is_compatible
    assert any("novos campos obrigatórios" in b for b in req_report.breaking)

    # Em response: adicionar campo obrigatório na resposta é não-quebrante
    resp_report = check_schema_compatibility(old, new, direction="response")
    assert resp_report.is_compatible
    assert any("novos campos obrigatórios" in nb for nb in resp_report.non_breaking)


def test_schema_compat_removed_required_fields_by_direction() -> None:
    """Verifica o comportamento de schema compat removed required fields by direction."""
    old = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
    }
    new = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": [],
    }
    # Em request: campo deixar de ser obrigatório é não-quebrante
    req_report = check_schema_compatibility(old, new, direction="request")
    assert req_report.is_compatible
    assert any("campos não mais obrigatórios" in nb for nb in req_report.non_breaking)

    # Em response: campo deixar de ser obrigatório é quebrante para clientes estritos
    resp_report = check_schema_compatibility(old, new, direction="response")
    assert not resp_report.is_compatible
    assert any(
        "campos não mais obrigatórios em response" in b for b in resp_report.breaking
    )


def test_schema_compat_nested_schemas_and_refs() -> None:
    """Verifica o comportamento de schema compat nested schemas and refs."""
    old = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"$ref": "#/defs/OldItem"}},
        },
    }
    new = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"$ref": "#/defs/NewItem"}},
        },
    }
    report = check_schema_compatibility(old, new)
    assert not report.is_compatible
    assert any("referência $ref alterada" in b for b in report.breaking)


def test_openapi_endpoint_and_request_schema_removal_detection() -> None:
    """Verifica o comportamento de openapi endpoint and request schema removal detection."""
    from devtools.contracts.__main__ import (
        _classify_openapi_schemas,
        _removed_operations,
        _removed_request_schemas,
    )

    baseline_openapi = {
        "paths": {
            "/api/v1/users": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/UserListResponse"
                                    }
                                }
                            }
                        }
                    }
                },
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/CreateUserRequest"
                                }
                            }
                        }
                    }
                },
            }
        },
        "components": {
            "schemas": {
                "CreateUserRequest": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
                "UserListResponse": {
                    "type": "object",
                    "properties": {"users": {"type": "array"}},
                },
            }
        },
    }

    new_openapi = {
        "paths": {
            "/api/v1/users": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/UserListResponse"
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "UserListResponse": {
                    "type": "object",
                    "properties": {"users": {"type": "array"}},
                },
            }
        },
    }

    req_schemas, resp_schemas = _classify_openapi_schemas(baseline_openapi)
    assert "CreateUserRequest" in req_schemas
    assert "UserListResponse" in resp_schemas

    # Verifica detecção de remoção de endpoint
    removed_endpoints = _removed_operations(baseline_openapi, new_openapi)
    assert removed_endpoints == [("POST", "/api/v1/users")]

    # Verifica detecção de remoção de request schema
    removed_req_schemas = _removed_request_schemas(baseline_openapi, new_openapi)
    assert removed_req_schemas == ["CreateUserRequest"]


def test_python_union_type_to_ts_preserves_nullability() -> None:
    """Verifica o comportamento de python union type to ts preserves nullability."""
    from devtools.contracts.__main__ import _python_type_to_ts

    assert _python_type_to_ts(str | None) == "string | null"


def test_schema_compat_combinators_compare_content_and_direction() -> None:
    """Verifica o comportamento de schema compat combinators compare content and direction."""
    old = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    removed = {"anyOf": [{"type": "string"}]}
    request_report = check_schema_compatibility(old, removed, direction="request")
    assert not request_report.is_compatible
    response_report = check_schema_compatibility(old, removed, direction="response")
    assert response_report.is_compatible

    changed = {"anyOf": [{"type": "number"}, {"type": "integer"}]}
    changed_report = check_schema_compatibility(old, changed, direction="request")
    assert not changed_report.is_compatible


def test_schema_compat_response_enum_expansion_is_breaking() -> None:
    """Verifica o comportamento de schema compat response enum expansion is breaking."""
    report = check_schema_compatibility(
        {"type": "string", "enum": ["a"]},
        {"type": "string", "enum": ["a", "b"]},
        direction="response",
    )
    assert not report.is_compatible


def test_schema_compat_combinator_removal_from_middle() -> None:
    """Verifica o comportamento de schema compat combinator removal from middle."""
    old = {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "boolean"}]}
    new = {"anyOf": [{"type": "string"}, {"type": "boolean"}]}

    request_report = check_schema_compatibility(old, new, direction="request")
    assert not request_report.is_compatible
    assert any("alternativa 1" in item for item in request_report.breaking)

    response_report = check_schema_compatibility(old, new, direction="response")
    assert response_report.is_compatible


def test_schema_compat_response_enum_expansion_inside_anyof_is_breaking() -> None:
    """A response alternative cannot add values hidden inside anyOf."""
    old = {"anyOf": [{"type": "string", "enum": ["a"]}]}
    new = {"anyOf": [{"type": "string", "enum": ["a", "b"]}]}

    report = check_schema_compatibility(old, new, direction="response")

    assert not report.is_compatible


def test_schema_compat_allof_reserves_exact_matches_before_compatible_pairs() -> None:
    """A broad alternative must not consume a later exact response pair."""
    old = {
        "allOf": [
            {"type": "object"},
            {"type": "object", "required": ["id"]},
        ]
    }
    new = {
        "allOf": [
            {"type": "object", "required": ["id"]},
            {"type": "object", "properties": {"b": {"type": "string"}}},
        ]
    }
    report = check_schema_compatibility(old, new, direction="response")
    assert report.is_compatible


def test_schema_compat_request_tightened_bounds_are_breaking() -> None:
    """Requests não podem perder valores aceitos por limites mais estreitos."""
    old = {
        "type": "object",
        "properties": {"name": {"type": "string", "minLength": 1}},
    }
    new = {
        "type": "object",
        "properties": {"name": {"type": "string", "minLength": 3}},
    }
    report = check_schema_compatibility(old, new, direction="request")
    assert not report.is_compatible
    assert any("minLength" in item for item in report.breaking)


def test_schema_compat_response_loosened_bounds_are_breaking() -> None:
    """Responses não podem começar a emitir valores fora do domínio anterior."""
    old = {"type": "array", "maxItems": 5}
    new = {"type": "array", "maxItems": 10}
    report = check_schema_compatibility(old, new, direction="response")
    assert not report.is_compatible
    assert any("maxItems" in item for item in report.breaking)


def test_schema_compat_additional_properties_respects_direction() -> None:
    """Fechar objetos quebra requests; abrir responses quebra consumidores estritos."""
    old = {"type": "object", "additionalProperties": True}
    new = {"type": "object", "additionalProperties": False}
    assert not check_schema_compatibility(old, new, direction="request").is_compatible

    old_response = {"type": "object", "additionalProperties": False}
    new_response = {"type": "object", "additionalProperties": True}
    assert not check_schema_compatibility(
        old_response, new_response, direction="response"
    ).is_compatible

    # An absent key means ``true`` in JSON Schema, so adding ``false`` to a
    # request or dropping it from a response is the same breaking change.
    implicit_open = {"type": "object"}
    closed = {"type": "object", "additionalProperties": False}
    assert not check_schema_compatibility(
        implicit_open, closed, direction="request"
    ).is_compatible
    assert not check_schema_compatibility(
        closed, implicit_open, direction="response"
    ).is_compatible
    assert check_schema_compatibility(
        closed, implicit_open, direction="request"
    ).is_compatible
    assert check_schema_compatibility(
        implicit_open, closed, direction="response"
    ).is_compatible
    assert check_schema_compatibility(
        implicit_open,
        {"type": "object", "additionalProperties": True},
        direction="request",
    ).is_compatible


def test_schema_compat_oneof_rejects_overlapping_new_alternative() -> None:
    """A new overlapping oneOf branch can make an old value ambiguous."""
    old = {"oneOf": [{"type": "integer"}]}
    new = {"oneOf": [{"type": "integer"}, {"type": "number"}]}

    report = check_schema_compatibility(old, new, direction="request")

    assert not report.is_compatible


def test_schema_compat_allof_reordering_is_not_breaking() -> None:
    """A ordem das restrições de allOf não altera o contrato."""
    old = {
        "allOf": [
            {"type": "object", "required": ["id"]},
            {"properties": {"id": {"type": "string"}}},
        ]
    }
    reordered = {"allOf": list(reversed(old["allOf"]))}

    report = check_schema_compatibility(old, reordered)

    assert report.is_compatible
    assert report.breaking == []


def test_schema_compat_allof_changed_constraint_is_breaking() -> None:
    """Reordenar é seguro, mas trocar uma restrição não é."""
    old = {
        "allOf": [
            {"type": "object", "required": ["id"]},
            {"properties": {"id": {"type": "string"}}},
        ]
    }
    changed = {
        "allOf": [
            {"type": "object", "required": ["id"]},
            {"properties": {"id": {"type": "integer"}}},
        ]
    }

    report = check_schema_compatibility(old, changed)

    assert not report.is_compatible


def test_schema_compat_removed_shared_definition_is_breaking() -> None:
    """Uma definição removida pode quebrar referências existentes."""
    old = {"$defs": {"Payload": {"type": "object"}}}
    new = {"$defs": {}}

    report = check_schema_compatibility(old, new)

    assert not report.is_compatible
    assert any("Payload" in item for item in report.breaking)


def test_contract_baseline_prefers_ci_base_and_explicit_override(monkeypatch) -> None:
    """Verifica o comportamento de contract baseline prefers ci base and explicit override."""
    from devtools.contracts.__main__ import _contract_baseline_ref

    monkeypatch.delenv("VECTORA_CONTRACT_BASE_REF", raising=False)
    monkeypatch.setenv("GITHUB_BASE_REF", "feat/contracts-python")
    assert _contract_baseline_ref() == "origin/feat/contracts-python"

    monkeypatch.setenv("VECTORA_CONTRACT_BASE_REF", "refs/remotes/origin/custom")
    assert _contract_baseline_ref() == "refs/remotes/origin/custom"


def test_baseline_contract_tree_is_detected(monkeypatch, tmp_path) -> None:
    """A release without the contract tree is a valid initialization baseline."""
    import devtools.contracts.__main__ as contracts_cli

    class Result:
        returncode = 0
        stdout = "backend/contracts\n"

    monkeypatch.setattr(
        contracts_cli.subprocess, "run", lambda *args, **kwargs: Result()
    )
    assert contracts_cli._baseline_contains_contracts(tmp_path, "origin/release/0.3")


def test_baseline_contract_tree_missing_is_not_treated_as_drift(
    monkeypatch, tmp_path
) -> None:
    """The first contracts export must not fail only because its base is empty."""
    import devtools.contracts.__main__ as contracts_cli

    class Result:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(
        contracts_cli.subprocess, "run", lambda *args, **kwargs: Result()
    )
    assert not contracts_cli._baseline_contains_contracts(
        tmp_path, "origin/release/0.3"
    )


@pytest.mark.parametrize(
    "constraint",
    [
        "minLength",
        "minItems",
        "minimum",
        "exclusiveMinimum",
        "maxLength",
        "maxItems",
        "maximum",
        "exclusiveMaximum",
    ],
)
@pytest.mark.parametrize("direction", ["request", "response"])
def test_numeric_bound_addition_and_removal(constraint, direction) -> None:
    """Adding a bound narrows a domain and removing it widens the domain."""
    unbounded = {"type": "number"}
    bounded = {"type": "number", constraint: 3}
    added = check_schema_compatibility(unbounded, bounded, direction=direction)
    removed = check_schema_compatibility(bounded, unbounded, direction=direction)
    assert added.is_compatible is (direction == "response")
    assert removed.is_compatible is (direction == "request")
    assert len(added.breaking) + len(added.non_breaking) == 1
    assert len(removed.breaking) + len(removed.non_breaking) == 1


@pytest.mark.parametrize(
    ("old", "new"), [(True, False), (True, 3), (3, False), (None, True), (False, None)]
)
@pytest.mark.parametrize("direction", ["request", "response"])
def test_boolean_exclusive_minimum_is_not_a_numeric_bound(old, new, direction) -> None:
    """Draft boolean exclusive bounds must never be compared as integers."""
    report = check_schema_compatibility(
        {"exclusiveMinimum": old},
        {"exclusiveMinimum": new},
        direction=direction,
    )
    assert report.breaking == []
    assert report.non_breaking == []


def test_unchanged_numeric_bound_produces_empty_report() -> None:
    """Par de erro: mesmo limite nos dois lados não é alteração."""
    old = {"type": "string", "minLength": 3}
    report = check_schema_compatibility(old, dict(old), direction="request")
    assert report.is_compatible
    assert report.breaking == [] and report.non_breaking == []


def test_removed_allof_alternative_is_classified_by_direction() -> None:
    """Request: remover uma das duas alternativas de allOf amplia o aceito
    (não quebra). Response: a mesma remoção estreita o que o cliente recebe."""
    old = {
        "allOf": [
            {"type": "object", "required": ["id"]},
            {"type": "object", "required": ["name"]},
        ]
    }
    new = {"allOf": [{"type": "object", "required": ["id"]}]}
    request = check_schema_compatibility(old, new, direction="request")
    assert not any("não possui correspondente" in msg for msg in request.breaking)
    assert any("não possui correspondente" in msg for msg in request.non_breaking)

    response = check_schema_compatibility(old, new, direction="response")
    assert any("não possui correspondente" in msg for msg in response.breaking)

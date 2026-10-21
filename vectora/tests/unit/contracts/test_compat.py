"""Testes unitários para verificação de compatibilidade semântica de schemas."""

from devtools.contracts.compat import check_schema_compatibility


def test_schema_compat_identical() -> None:
    s1 = {"type": "object", "properties": {"a": {"type": "string"}}}
    s2 = {"type": "object", "properties": {"a": {"type": "string"}}}
    report = check_schema_compatibility(s1, s2)
    assert report.is_compatible
    assert len(report.breaking) == 0
    assert len(report.non_breaking) == 0


def test_schema_compat_added_field() -> None:
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
    old = {"type": "object", "properties": {"a": {"type": "string"}}}
    new = {"type": "object", "properties": {"a": {"type": "integer"}}}
    report = check_schema_compatibility(old, new)
    assert not report.is_compatible
    assert len(report.breaking) == 1
    assert "tipo alterado" in report.breaking[0]


def test_schema_compat_required_fields_by_direction() -> None:
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

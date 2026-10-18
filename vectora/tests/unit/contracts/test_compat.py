"""Testes unitários para verificação de compatibilidade semântica de schemas."""

from devtools.contracts.compat import check_schema_compatibility


def test_schema_compat_identical():
    s1 = {"type": "object", "properties": {"a": {"type": "string"}}}
    s2 = {"type": "object", "properties": {"a": {"type": "string"}}}
    report = check_schema_compatibility(s1, s2)
    assert report.is_compatible
    assert len(report.breaking) == 0
    assert len(report.non_breaking) == 0


def test_schema_compat_added_field():
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


def test_schema_compat_removed_field():
    old = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
    }
    new = {"type": "object", "properties": {"a": {"type": "string"}}}
    report = check_schema_compatibility(old, new)
    assert not report.is_compatible
    assert len(report.breaking) == 1
    assert "b" in report.breaking[0]


def test_schema_compat_type_changed():
    old = {"type": "object", "properties": {"a": {"type": "string"}}}
    new = {"type": "object", "properties": {"a": {"type": "integer"}}}
    report = check_schema_compatibility(old, new)
    assert not report.is_compatible
    assert len(report.breaking) == 1
    assert "tipo alterado" in report.breaking[0]

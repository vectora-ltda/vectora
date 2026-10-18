"""Testes de conformidade do schema de eventos SSE."""

import json
from pathlib import Path


def test_sse_schema_exists_and_valid():
    schema_path = (
        Path(__file__).resolve().parents[3]
        / "backend"
        / "contracts"
        / "sse"
        / "events.schema.json"
    )
    assert schema_path.exists(), f"SSE schema file not found at {schema_path}"
    data = json.loads(schema_path.read_text(encoding="utf-8"))
    assert "$defs" in data or "anyOf" in data or "oneOf" in data

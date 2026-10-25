"""Testes de conformidade do schema de eventos SSE."""

import json
from pathlib import Path


def test_sse_schema_exists_and_valid() -> None:
    """Verifica o comportamento de sse schema exists and valid."""
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

    defs = data.get("$defs", {})
    for ref_entry in data.get("anyOf", []):
        ref = ref_entry.get("$ref", "")
        def_name = ref.split("/")[-1]
        assert def_name in defs, (
            f"Definição {def_name} referenciada em anyOf ausente em $defs"
        )
        event_def = defs[def_name]
        props = event_def.get("properties", {})
        assert "type" in props, f"Propriedade 'type' ausente em {def_name}"
        assert "const" in props["type"], (
            f"Campo 'const' ausente em {def_name}.properties.type"
        )
        assert "type" in event_def.get("required", []), (
            f"'type' não está em required de {def_name}"
        )


def test_sse_wire_format_deterministic_encoding() -> None:
    """Verifica o comportamento de sse wire format deterministic encoding."""
    from backend.api.schemas import DoneEvent, TokenEvent, ToolCallEvent, encode_event

    # 1. TokenEvent
    tok_payload = TokenEvent(content="Hello world", node="generator")
    encoded_tok = encode_event(tok_payload)
    assert encoded_tok.startswith("data: ")
    assert encoded_tok.endswith("\n\n")
    data_tok = json.loads(encoded_tok.removeprefix("data: ").removesuffix("\n\n"))
    assert data_tok["type"] == "token"
    assert data_tok["content"] == "Hello world"
    assert data_tok["node"] == "generator"

    # 2. ToolCallEvent carregando identidade e render_hint
    tool_payload = ToolCallEvent(
        tool_name="read_file",
        tool_call_id="call-12345",
        args_json='{"path": "file.py"}',
        render_hint="file",
        destructive=False,
    )
    encoded_tool = encode_event(tool_payload)
    data_tool = json.loads(encoded_tool.removeprefix("data: ").removesuffix("\n\n"))
    assert data_tool["type"] == "tool_call"
    assert data_tool["tool_call_id"] == "call-12345"
    assert data_tool["render_hint"] == "file"
    assert data_tool["destructive"] is False

    # 3. DoneEvent carregando thread_id e run_id
    done_payload = DoneEvent(thread_id="t-1234", run_id="r-5678")
    encoded_done = encode_event(done_payload)
    data_done = json.loads(encoded_done.removeprefix("data: ").removesuffix("\n\n"))
    assert data_done["type"] == "done"
    assert data_done["thread_id"] == "t-1234"
    assert data_done["run_id"] == "r-5678"


def test_sse_error_codes_in_matrix() -> None:
    """Verifica o comportamento de sse error codes in matrix."""
    matrix_path = (
        Path(__file__).resolve().parents[3]
        / "backend"
        / "contracts"
        / "errors"
        / "matrix.json"
    )
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))

    # Verifica que códigos emitidos em handlers/chat e stream nativo têm entrada na matriz
    emitted_codes = {
        "MODEL_NO_VISION",
        "MODEL_NO_TOOL_CALLING",
        "MODEL_NOT_ALLOWED",
        "EMPTY_MODEL_RESPONSE",
        "INTERNAL",
        "INTERNAL_ERROR",
        "UNAUTHORIZED",
        "FORBIDDEN",
        "NOT_FOUND",
        "RATE_LIMITED",
        "SERVICE_UNAVAILABLE",
    }
    for code in emitted_codes:
        assert code in matrix, f"Código de erro {code} não documentado em matrix.json"
        entry = matrix[code]
        assert "category" in entry
        assert "retryable" in entry
        assert "status_code" in entry

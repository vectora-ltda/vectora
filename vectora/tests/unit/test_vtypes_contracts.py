"""Regressões dos contratos estruturais compartilhados do motor nativo."""

from __future__ import annotations

from typing import cast

from backend.vtypes.contracts import (
    ApprovalMetadata,
    ChangedFile,
    JsonObject,
    ToolOption,
)
from backend.vtypes.message import ToolCall


def test_tool_call_args_usa_objeto_json_e_opcoes_tem_shape_estavel() -> None:
    args = {"path": "README.md", "line": 1, "options": [{"label": "ok", "value": "ok"}]}
    call = ToolCall(id="call-1", name="file_read", args=cast("JsonObject", args))

    assert call.args["path"] == "README.md"
    assert ToolOption.__required_keys__ == frozenset({"label", "value"})


def test_envelopes_de_aprovacao_e_arquivo_exigem_apenas_campos_compartilhados() -> None:
    metadata: ApprovalMetadata = {
        "operation": "generate_image",
        "estimated_units": 1,
    }
    changed: ChangedFile = {"path": "vectora/backend/vtypes/contracts.py"}

    assert metadata["operation"] == "generate_image"
    assert changed["path"].endswith("contracts.py")

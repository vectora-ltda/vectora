"""Imagens devolvidas por ferramentas MCP chegam ao modelo como imagem."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services.assets import AssetStore
from backend.services.tool_media import tool_image_blocks
from backend.settings import settings
from backend.tools.context import ToolContext
from backend.tools.mcp import VectoraMCPClient, _render_call_result

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16
_DUMMY_SERVER = str(
    Path(__file__).resolve().parent.parent / "fixtures" / "dummy_mcp_server.py"
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "vectora_home", tmp_path)
    monkeypatch.setattr("backend.services.assets.asset_store", AssetStore())
    return tmp_path


def _ctx() -> ToolContext:
    return ToolContext(user_id="u1", workspace_id="w1", thread_id="t1")


def _image(data: bytes = PNG) -> SimpleNamespace:
    return SimpleNamespace(
        type="image", data=base64.b64encode(data).decode(), mimeType="image/png"
    )


def test_imagens_viram_assets_e_nao_entram_em_base64(home: Path) -> None:
    result = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="duas capturas"),
            _image(),
            _image(),
        ],
        isError=False,
    )
    rendered = _render_call_result(result, _ctx())

    data = json.loads(rendered)
    assert data["text"].startswith("duas capturas")
    assert data["url"].startswith("/threads/t1/assets/")
    assert base64.b64encode(PNG).decode() not in rendered
    blocks = tool_image_blocks(rendered)
    assert len(blocks) == 2
    assert all(block.kind == "image_url" and block.asset_id for block in blocks)


def test_sem_contexto_audio_e_recurso_viram_nota() -> None:
    result = SimpleNamespace(
        content=[
            _image(),
            SimpleNamespace(type="audio", data="AAAA", mimeType="audio/wav"),
            SimpleNamespace(
                type="resource",
                resource=SimpleNamespace(uri="file:///x.bin", blob="AAAA"),
            ),
        ],
        isError=False,
    )
    rendered = _render_call_result(result)
    assert "AAAA" not in rendered
    assert "não pôde ser salva" in rendered
    assert "áudio retornado" in rendered
    assert "recurso binário: file:///x.bin" in rendered
    assert tool_image_blocks(rendered) == []


def test_erro_da_tool_nao_anexa_imagem(home: Path) -> None:
    result = SimpleNamespace(content=[_image()], isError=True)
    rendered = _render_call_result(result, _ctx())
    assert rendered.startswith("Erro da tool MCP:")
    assert tool_image_blocks(rendered) == []


async def test_servidor_mcp_real_com_image_content(home: Path) -> None:
    client = VectoraMCPClient()
    try:
        await client.connect(
            {
                "dummy": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [_DUMMY_SERVER],
                }
            }
        )
        rendered = await client.call_tool("screenshot", {}, _ctx())
    finally:
        await client.aclose()

    data = json.loads(rendered)
    assert "captura pronta" in data["text"]
    [block] = tool_image_blocks(rendered)
    assert block.attachment_mime_type == "image/png"
    assert list((home / "artifacts" / "t1" / "media").iterdir())


async def test_tool_de_plugin_repassa_o_contexto(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.workspace import plugins

    received: list[object] = []

    async def fake_call(self, tool_name, arguments, ctx=None):
        received.append((tool_name, arguments, ctx))
        return "ok"

    async def fake_connect(self, connections, strict=False):
        return None

    monkeypatch.setattr(plugins.VectoraMCPClient, "call_tool", fake_call)
    monkeypatch.setattr(plugins.VectoraMCPClient, "connect", fake_connect)
    monkeypatch.setattr(
        plugins.mcp_policy,
        "evaluate",
        lambda *_a, **_k: SimpleNamespace(allowed=True),
    )
    monkeypatch.setattr(plugins.tool_policy, "is_allowed", lambda *_a: True)

    tool = SimpleNamespace(
        name="capturar",
        description="",
        input_schema={"type": "object", "properties": {"url": {"type": "string"}}},
    )
    spec = plugins._remote_tool_spec("srv", {"transport": "stdio"}, tool, "u1", "w1")
    ctx = _ctx()
    assert await spec.ainvoke({"url": "https://x"}, ctx) == "ok"
    assert received == [("capturar", {"url": "https://x"}, ctx)]
    assert "__vectora_tool_context" not in spec.openai_schema()["function"][
        "parameters"
    ].get("properties", {})

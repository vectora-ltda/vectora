"""Imagens de ferramentas (screenshots) chegam ao modelo como imagem."""

from __future__ import annotations

import base64

import pytest

from backend.llm.media_parts import downgrade_to_text, has_native_media
from backend.services.media_hydration import (
    MAX_RECENT_TOOL_IMAGES,
    hydrate_media_blocks,
)
from backend.vtypes.message import (
    ContentBlock,
    MessageRole,
    ToolCall,
    VMessage,
    VMessageChunk,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16
PNG_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()


def _tool(
    call_id: str, *, image_url: str | None = PNG_URL, asset: str = "a1"
) -> VMessage:
    return VMessage(
        role=MessageRole.TOOL,
        tool_call_id=call_id,
        name="browser_screenshot",
        content=[
            ContentBlock(kind="text", text='{"status": "ok"}'),
            ContentBlock(
                kind="image_url",
                image_url=image_url,
                asset_id=asset,
                attachment_name=f"{call_id}.png",
                attachment_mime_type="image/png",
            ),
        ],
    )


def _assistant(*call_ids: str) -> VMessage:
    return VMessage(
        role=MessageRole.ASSISTANT,
        tool_calls=[
            ToolCall(id=cid, name="browser_screenshot", args={}) for cid in call_ids
        ],
    )


def test_anthropic_tool_result_leva_imagem() -> None:
    from backend.llm.anthropic.chat_client import _to_anthropic_messages

    _system, messages = _to_anthropic_messages([_assistant("c1"), _tool("c1")])
    result = messages[1]["content"][0]
    assert result["type"] == "tool_result"
    assert result["content"][0] == {"type": "text", "text": '{"status": "ok"}'}
    assert result["content"][1]["type"] == "image"
    assert result["content"][1]["source"]["media_type"] == "image/png"


def test_openai_function_call_output_leva_input_image() -> None:
    from backend.llm.openai.chat_client import _to_openai_input

    items = _to_openai_input([_assistant("c1"), _tool("c1")])
    output = next(i for i in items if i.get("type") == "function_call_output")
    assert output["output"][1] == {"type": "input_image", "image_url": PNG_URL}


def test_gemini_function_response_com_parte_inline() -> None:
    from backend.llm.google.chat_client import _to_google_contents

    _system, contents = _to_google_contents([_assistant("c1"), _tool("c1")])
    parts = contents[-1]["parts"]
    assert "functionResponse" in parts[0]
    assert parts[1]["inlineData"]["mimeType"] == "image/png"


def test_openrouter_imagem_vai_em_mensagem_de_usuario_depois_das_tools() -> None:
    from backend.llm.openrouter.chat_client import _to_openrouter_messages

    payload = _to_openrouter_messages(
        [_assistant("c1", "c2"), _tool("c1"), _tool("c2", asset="a2")]
    )
    roles = [message["role"] for message in payload]
    assert roles == ["assistant", "tool", "tool", "user"]
    assert payload[1]["content"] == '{"status": "ok"}'
    images = [part for part in payload[3]["content"] if part["type"] == "image_url"]
    assert len(images) == 2


def test_so_as_imagens_de_ferramenta_mais_recentes_sao_carregadas() -> None:
    total = MAX_RECENT_TOOL_IMAGES + 2
    messages = [_tool(f"c{i}", image_url=None, asset=f"a{i}") for i in range(total)]
    reads: list[str] = []

    def reader(asset_id: str, **_kwargs: object) -> tuple[object, bytes]:
        reads.append(asset_id)
        return object(), PNG

    sent = hydrate_media_blocks(
        messages, user_id="u", workspace_id="", thread_id="t", reader=reader
    )
    assert reads == [f"a{i}" for i in range(2, total)]
    assert sent[0].content[1].kind == "text"
    assert "omitida" in (sent[0].content[1].text or "")
    assert sent[-1].content[1].image_url == PNG_URL


def test_downgrade_troca_imagem_de_ferramenta_por_nota() -> None:
    messages = [_assistant("c1"), _tool("c1")]
    assert has_native_media(messages)
    downgraded = downgrade_to_text(messages)
    assert not has_native_media(downgraded)
    assert downgraded[1].content[1].kind == "text"
    assert "c1.png" in (downgraded[1].content[1].text or "")


async def test_provider_que_recusa_imagem_de_ferramenta_recebe_o_turno_sem_ela(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.llm.fallback_chat_client import FallbackChatClient

    received: list[list[VMessage]] = []

    class _Client:
        calls = 0

        async def agenerate(self, messages, **_kwargs):
            raise NotImplementedError

        async def astream(
            self, messages, *, tools=None, temperature=None, max_tokens=None
        ):
            received.append(list(messages))
            _Client.calls += 1
            if _Client.calls == 1:
                raise RuntimeError(
                    "400: tool message content must be a string, image not allowed"
                )
            yield VMessageChunk(delta_text="ok")

    async def candidates(*_args: object, **_kwargs: object) -> list[str]:
        return ["openrouter:modelo"]

    monkeypatch.setattr("backend.llm.fallback_chat_client._candidates", candidates)
    monkeypatch.setattr(
        "backend.llm.fallback_chat_client.load_chat_client", lambda _mid: _Client()
    )
    client = FallbackChatClient("openrouter:modelo")
    chunks = [c async for c in client.astream([_assistant("c1"), _tool("c1")])]

    assert [c.delta_text for c in chunks] == ["ok"]
    assert client.last_media_downgraded is True
    assert received[1][1].content[1].kind == "text"


async def test_tool_batch_anexa_imagem_pedida_pelo_resultado() -> None:
    from pydantic import BaseModel

    from backend.engine.tool_batch import execute_tool_batch
    from backend.tools.context import ToolContext
    from backend.tools.registry import ToolExtras, ToolRegistry, ToolSpec

    async def fake_screenshot(ctx: ToolContext) -> str:
        return (
            '{"status": "ok", "url": "/threads/t/assets/x", "model_image": '
            '{"asset_id": "x", "name": "s.png", "mime_type": "image/png", '
            '"size_bytes": 9}}'
        )

    class _NoArgs(BaseModel):
        pass

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="fake_screenshot",
            description="Screenshot falso.",
            args_model=_NoArgs,
            handler=fake_screenshot,
            extras=ToolExtras(render_hint="image_preview", category="browser"),
            needs_ctx=True,
        )
    )

    [message] = await execute_tool_batch(
        [ToolCall(id="c1", name="fake_screenshot", args={})],
        tool_registry=registry,
        ctx=ToolContext(thread_id="t"),
    )
    assert [block.kind for block in message.content] == ["text", "image_url"]
    assert message.content[1].asset_id == "x"
    assert message.content[1].image_url is None

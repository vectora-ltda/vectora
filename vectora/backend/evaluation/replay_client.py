"""Cliente de chat determinístico para replay de cassetes sem dependência externa."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from backend.evaluation.cassette import Cassette, CassetteInteraction
from backend.llm.base import ChatClient
from backend.vtypes import JsonObject
from backend.vtypes.message import (
    ContentBlock,
    MessageRole,
    ToolCall,
    ToolCallChunk,
    UsageSnapshot,
    VMessage,
    VMessageChunk,
)

if TYPE_CHECKING:
    from backend.tools.registry import ToolSpec


class ReplayChatClient(ChatClient):
    """ChatClient determinístico que reproduz sequencialmente interações salvas por cursor posicional."""

    def __init__(self, cassette: Cassette, *, fallback_on_missing: bool = False):
        """Inicializa o estado da instância."""
        self.cassette = cassette
        self.fallback_on_missing = fallback_on_missing
        self._interaction_cursor = 0

    def reset(self) -> None:
        """Reinicia o cursor sequencial de interações."""
        self._interaction_cursor = 0

    @staticmethod
    def _message_key(message: VMessage | dict[str, Any]) -> str:
        """Canonicaliza apenas o conteúdo observável usado pelo replay.

        IDs de mensagens e campos de transporte variam entre clientes; papel,
        texto, chamadas e argumentos formam a entrada determinística da cassete.
        """
        if isinstance(message, VMessage):
            value: dict[str, Any] = {
                "role": message.role.value,
                "content": message.text(),
                "tool_calls": [
                    {"name": call.name, "args": call.args}
                    for call in message.tool_calls
                ],
                "tool_call_id": message.tool_call_id,
                "name": message.name,
            }
        else:
            content = message.get("content", "")
            if isinstance(content, list):
                content = "".join(
                    str(block.get("text", ""))
                    for block in content
                    if isinstance(block, dict)
                )
            value = {
                "role": str(message.get("role", "user")),
                "content": str(content),
                "tool_calls": [
                    {
                        "name": str(call.get("name", "")),
                        "args": call.get("args", {}),
                    }
                    for call in message.get("tool_calls", [])
                    if isinstance(call, dict)
                ],
                "tool_call_id": message.get("tool_call_id"),
                "name": message.get("name"),
            }
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)

    def _resolve_next_interaction(
        self, messages: list[VMessage]
    ) -> CassetteInteraction | None:
        """Resolve a interação correspondente por cursor sequencial ou chave."""
        if self._interaction_cursor < len(self.cassette.interactions):
            interaction = self.cassette.interactions[self._interaction_cursor]
            expected = [self._message_key(item) for item in interaction.messages]
            actual = [self._message_key(item) for item in messages]
            if expected and expected != actual:
                raise RuntimeError(
                    "ReplayChatClient: histórico não corresponde à interação "
                    f"'{interaction.interaction_id}'"
                )
            self._interaction_cursor += 1
            return interaction
        return None

    def _interaction_to_vmessage(self, item: CassetteInteraction) -> VMessage:
        """Executa a operação interaction to vmessage."""
        content_blocks: list[ContentBlock] = []
        if item.response_text:
            content_blocks.append(ContentBlock(kind="text", text=item.response_text))

        tool_calls: list[ToolCall] = []
        for tc in item.tool_calls:
            raw_args = tc.get("args")
            args: JsonObject = raw_args if isinstance(raw_args, dict) else {}
            tool_calls.append(
                ToolCall(
                    id=str(tc.get("id", f"call_{len(tool_calls)}")),
                    name=str(tc.get("name", "")),
                    args=args,
                )
            )

        return VMessage(
            role=MessageRole.ASSISTANT,
            content=content_blocks,
            tool_calls=tool_calls,
        )

    async def astream(
        self,
        messages: list[VMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[VMessageChunk]:
        """Transmite chunks simulados com base na interação gravada."""
        interaction = self._resolve_next_interaction(messages)
        if interaction is None:
            if not self.fallback_on_missing:
                raise RuntimeError(
                    f"ReplayChatClient: nenhuma interação correspondente na cassete '{self.cassette.cassette_id}'"
                )
            yield VMessageChunk(
                delta_text="[Replay fallback: interação não encontrada]"
            )
            return

        if interaction.response_text:
            yield VMessageChunk(delta_text=interaction.response_text)

        for idx, tc in enumerate(interaction.tool_calls):
            yield VMessageChunk(
                tool_call_chunks=[
                    ToolCallChunk(
                        index=idx,
                        id=str(tc.get("id", f"call_{idx}")),
                        name=str(tc.get("name", "")),
                        args_fragment=json.dumps(tc.get("args", {})),
                    )
                ]
            )

        if interaction.usage:
            u = interaction.usage
            inp = int(u.get("input", u.get("prompt", u.get("input_tokens", 0))))
            out = int(u.get("output", u.get("completion", u.get("output_tokens", 0))))
            tot = int(u.get("total", u.get("total_tokens", inp + out)))
            yield VMessageChunk(
                usage=UsageSnapshot(
                    input_tokens=inp,
                    output_tokens=out,
                    total_tokens=tot,
                )
            )

    async def agenerate(
        self,
        messages: list[VMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> VMessage:
        """Gera a mensagem do turno a partir da cassete gravada."""
        interaction = self._resolve_next_interaction(messages)
        if interaction is None:
            if not self.fallback_on_missing:
                raise RuntimeError(
                    f"ReplayChatClient: interação esgotada na cassete '{self.cassette.cassette_id}'"
                )
            return VMessage(
                role=MessageRole.ASSISTANT,
                content=[ContentBlock(kind="text", text="[Replay esgotado]")],
            )
        return self._interaction_to_vmessage(interaction)

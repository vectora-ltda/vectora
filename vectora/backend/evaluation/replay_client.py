"""Cliente de chat determinístico para replay de cassetes sem dependência externa."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from backend.evaluation.cassette import Cassette, CassetteInteraction
from backend.llm.base import ChatClient
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
        self.cassette = cassette
        self.fallback_on_missing = fallback_on_missing
        self._interaction_cursor = 0

    def reset(self) -> None:
        """Reinicia o cursor sequencial de interações."""
        self._interaction_cursor = 0

    def _resolve_next_interaction(
        self, messages: list[VMessage]
    ) -> CassetteInteraction | None:
        """Resolve a interação correspondente por cursor sequencial ou chave."""
        if self._interaction_cursor < len(self.cassette.interactions):
            interaction = self.cassette.interactions[self._interaction_cursor]
            self._interaction_cursor += 1
            return interaction
        return None

    def _interaction_to_vmessage(self, item: CassetteInteraction) -> VMessage:
        content_blocks: list[ContentBlock] = []
        if item.response_text:
            content_blocks.append(ContentBlock(kind="text", text=item.response_text))

        tool_calls: list[ToolCall] = []
        for tc in item.tool_calls:
            tool_calls.append(
                ToolCall(
                    id=str(tc.get("id", f"call_{len(tool_calls)}")),
                    name=str(tc.get("name", "")),
                    args=dict(tc.get("args", {})),
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
            inp = int(
                interaction.usage.get("input", interaction.usage.get("prompt", 0))
            )
            out = int(
                interaction.usage.get("output", interaction.usage.get("completion", 0))
            )
            tot = int(interaction.usage.get("total", inp + out))
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

"""``OpenAIChatClient`` — chat nativo da OpenAI (Responses API), implementa
o Protocol ``ChatClient`` (``backend/llm/base.py``), consumido diretamente
pelo motor nativo (``backend/engine/conversation_loop.py``).

A Responses API é o sucessor recomendado da Chat Completions desde 2026 —
usa `input`/`output` (items) em vez de `messages`/`choices`, e streaming por
eventos discretos tipados em vez de deltas por campo único.

Edge case confirmado por pesquisa da documentação oficial: o servidor pode
emitir ``response.function_call_arguments.done`` **sem nenhum delta
anterior** — nesse caso o JSON completo já vem no próprio evento `.done`.
O parser não assume que sempre há deltas pra acumular.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from backend.llm.openai.client import OpenAIClient, OpenAIResponseError
from backend.vtypes.contracts import UsageSnapshot
from backend.vtypes.message import (
    ContentBlock,
    MessageRole,
    ToolCall,
    ToolCallChunk,
    VMessage,
    VMessageChunk,
    coerce_json_object,
)

if TYPE_CHECKING:
    from backend.tools.registry import ToolSpec

logger = logging.getLogger(__name__)


def _normalize_strict_schema(schema: dict) -> dict:
    """Aplica os requisitos do `strict:true` da Responses API recursivamente:
    `additionalProperties:false` em todo objeto, e todo campo em `required`
    (campos opcionais viram `type:[tipo, "null"]`)."""
    schema = dict(schema)
    if schema.get("type") == "object" and "properties" in schema:
        props = schema["properties"]
        original_required = set(schema.get("required") or [])
        novo_props: dict[str, Any] = {}
        for nome, subschema in props.items():
            sub = (
                _normalize_strict_schema(subschema)
                if isinstance(subschema, dict)
                else subschema
            )
            if (
                nome not in original_required
                and isinstance(sub, dict)
                and "type" in sub
            ):
                tipo = sub["type"]
                if isinstance(tipo, str) and tipo != "null":
                    sub = {**sub, "type": [tipo, "null"]}
            novo_props[nome] = sub
        schema["properties"] = novo_props
        schema["required"] = list(props.keys())
        schema["additionalProperties"] = False
    return schema


def _to_openai_tool(spec: ToolSpec) -> dict:
    """Converte um ``ToolSpec`` pro formato flat da Responses API
    (`{type, name, description, parameters, strict}`) — diferente do formato
    aninhado `{type:"function", function:{...}}` da Chat Completions.
    ``ToolSpec.openai_schema()`` (``backend/tools/registry.py``) já produz
    esse formato aninhado — só desembrulha e normaliza pro strict mode."""
    convertida = spec.openai_schema()
    funcao = convertida["function"]
    parametros = funcao.get("parameters") or {"type": "object", "properties": {}}
    return {
        "type": "function",
        "name": funcao.get("name", ""),
        "description": funcao.get("description", ""),
        "parameters": _normalize_strict_schema(parametros),
        "strict": True,
    }


_ROLE_POR_TIPO = {
    MessageRole.SYSTEM: "system",
    MessageRole.USER: "user",
    MessageRole.ASSISTANT: "assistant",
}


def _to_openai_content(content: list[ContentBlock]) -> str | list[dict]:
    """Bloco `text` vira string simples (caso comum); qualquer bloco
    `image_url` presente força o formato de lista de partes da Responses API
    (`input_text`/`input_image`) — nunca descarta o bloco de imagem só
    porque `msg.text()` só concatena texto."""
    if not any(b.kind == "image_url" for b in content):
        return "".join(b.text or "" for b in content if b.kind == "text")
    partes: list[dict] = []
    for bloco in content:
        if bloco.kind == "text" and bloco.text:
            partes.append({"type": "input_text", "text": bloco.text})
        elif bloco.kind == "image_url" and bloco.image_url:
            partes.append({"type": "input_image", "image_url": bloco.image_url})
    return partes


def _to_openai_input(messages: list[VMessage]) -> list[dict]:
    """Traduz ``VMessage`` pro formato `input` (lista de items) da Responses
    API. Tool calls viram items `function_call` separados; results de tool
    viram `function_call_output` — não são mensagens de role `tool` como na
    Chat Completions. Blocos `image_url` no content passam por
    ``_to_openai_content`` — nunca são descartados silenciosamente."""
    itens: list[dict] = []
    for msg in messages:
        if msg.role == MessageRole.TOOL:
            itens.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.tool_call_id or "",
                    "output": msg.text(),
                }
            )
            continue

        role = _ROLE_POR_TIPO.get(msg.role)
        if role is None:
            msg_erro = f"Mensagem de role {msg.role!r} não tem equivalente na OpenAI"
            raise ValueError(msg_erro)

        if role == "assistant" and msg.tool_calls:
            texto = msg.text()
            if texto:
                itens.append({"role": "assistant", "content": texto})
            itens.extend(
                {
                    "type": "function_call",
                    "call_id": tc.id,
                    "name": tc.name,
                    "arguments": json.dumps(tc.args, ensure_ascii=False),
                }
                for tc in msg.tool_calls
            )
            continue

        itens.append({"role": role, "content": _to_openai_content(msg.content)})
    return itens


def _parse_output_items(output: list) -> tuple[str, list[ToolCall]]:
    texto_partes: list[str] = []
    chamadas: list[ToolCall] = []
    for item in output or []:
        tipo = item.get("type")
        if tipo == "message":
            texto_partes.extend(
                bloco.get("text", "")
                for bloco in item.get("content") or []
                if bloco.get("type") == "output_text"
            )
        elif tipo == "function_call":
            args_texto = item.get("arguments") or "{}"
            try:
                args = coerce_json_object(json.loads(args_texto), raw=args_texto)
            except (json.JSONDecodeError, TypeError):
                # JSON inválido do modelo é um caso real, não hipotético —
                # a tool call entra mesmo assim (preserva o texto já gerado
                # no turno); ToolSpec.ainvoke rejeita `_parse_error` na
                # validação Pydantic dos args reais e devolve erro tipado,
                # em vez de a chamada inteira desaparecer silenciosamente.
                args = {"_parse_error": args_texto}
            chamadas.append(
                ToolCall(
                    id=item.get("call_id") or "", name=item.get("name", ""), args=args
                )
            )
    return "".join(texto_partes), chamadas


def _usage_metadata(usage: dict | None) -> UsageSnapshot | None:
    if not usage:
        return None
    entrada = int(usage.get("input_tokens") or 0)
    saida = int(usage.get("output_tokens") or 0)
    return {
        "input_tokens": entrada,
        "output_tokens": saida,
        "total_tokens": int(usage.get("total_tokens") or entrada + saida),
    }


class OpenAIChatClient:
    """Chat client nativo da OpenAI (Responses API) — implementa o Protocol
    ``ChatClient`` (``backend/llm/base.py``), async-only (CLAUDE.md regra 10).
    Diferente de ``VectoraOpenAIChat``, não há ``bind_tools()``: cada
    chamada de ``astream``/``agenerate`` recebe ``tools=`` diretamente."""

    def __init__(self, model: str, client: OpenAIClient) -> None:
        self.model = model
        self.client = client

    def _payload(
        self,
        messages: list[VMessage],
        *,
        tools: list[ToolSpec] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict:
        corpo: dict[str, Any] = {
            "model": self.model,
            "input": _to_openai_input(messages),
        }
        if temperature is not None:
            corpo["temperature"] = temperature
        if max_tokens is not None:
            corpo["max_output_tokens"] = max_tokens
        if tools:
            corpo["tools"] = [_to_openai_tool(t) for t in tools]
        return corpo

    async def agenerate(
        self,
        messages: list[VMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> VMessage:
        corpo = self._payload(
            messages, tools=tools, temperature=temperature, max_tokens=max_tokens
        )
        resposta = await self.client.create_response(corpo)
        output = resposta.get("output")
        if not isinstance(output, list):
            msg = (
                "OpenAI respondeu sem `output` utilizável — resposta "
                f"inutilizável (id={resposta.get('id')!r})"
            )
            raise OpenAIResponseError(msg)

        texto, chamadas = _parse_output_items(output)
        return VMessage(
            role=MessageRole.ASSISTANT,
            content=[ContentBlock(kind="text", text=texto)] if texto else [],
            tool_calls=chamadas,
            finish_reason="tool_calls" if chamadas else "stop",
        )

    async def astream(
        self,
        messages: list[VMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[VMessageChunk]:
        corpo = self._payload(
            messages, tools=tools, temperature=temperature, max_tokens=max_tokens
        )

        # Acumula por item_id — `response.output_item.added` cria a entrada
        # (name/call_id), `response.function_call_arguments.delta` fragmenta
        # os argumentos por múltiplos eventos, mapeados pro `index` que o
        # caller (backend/engine/conversation_loop.py) usa pra remontar a
        # tool call a partir dos ToolCallChunk acumulados.
        indices_por_item: dict[str, int] = {}
        nomes_por_item: dict[str, str] = {}
        call_ids_por_item: dict[str, str] = {}
        proximo_index = 0
        recebeu_delta: set[str] = set()
        nome_ja_emitido: set[str] = set()

        async for evento in self.client.stream_response(corpo):
            tipo = evento.get("type")

            if tipo == "response.output_item.added":
                item = evento.get("item") or {}
                if item.get("type") == "function_call":
                    item_id = item.get("id") or ""
                    indices_por_item[item_id] = proximo_index
                    nomes_por_item[item_id] = item.get("name", "")
                    call_ids_por_item[item_id] = item.get("call_id", "")
                    proximo_index += 1

            elif tipo == "response.function_call_arguments.delta":
                item_id = evento.get("item_id") or ""
                if item_id not in indices_por_item:
                    continue
                recebeu_delta.add(item_id)
                nome = (
                    nomes_por_item.get(item_id)
                    if item_id not in nome_ja_emitido
                    else None
                )
                nome_ja_emitido.add(item_id)
                yield VMessageChunk(
                    tool_call_chunks=[
                        ToolCallChunk(
                            index=indices_por_item[item_id],
                            id=call_ids_por_item.get(item_id),
                            name=nome,
                            args_fragment=evento.get("delta", ""),
                        )
                    ]
                )

            elif tipo == "response.function_call_arguments.done":
                item_id = evento.get("item_id") or ""
                if item_id not in indices_por_item or item_id in recebeu_delta:
                    # Já veio fragmentado via delta — o `.done` só confirma
                    # o fim, o caller já acumulou o JSON completo.
                    continue
                # Edge case: nenhum delta chegou antes do `.done` — o JSON
                # completo está no próprio evento.
                yield VMessageChunk(
                    tool_call_chunks=[
                        ToolCallChunk(
                            index=indices_por_item[item_id],
                            id=call_ids_por_item.get(item_id),
                            name=nomes_por_item.get(item_id),
                            args_fragment=evento.get("arguments", ""),
                        )
                    ]
                )

            elif tipo == "response.output_text.delta":
                texto = evento.get("delta", "")
                if texto:
                    yield VMessageChunk(delta_text=texto)

            elif tipo == "response.completed":
                resposta = evento.get("response") or {}
                usage = resposta.get("usage")
                if usage:
                    yield VMessageChunk(usage=_usage_metadata(usage))

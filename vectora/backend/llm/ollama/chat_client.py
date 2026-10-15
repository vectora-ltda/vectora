"""``OllamaChatClient`` — chat nativo do Ollama (``POST /api/chat``),
implementa o Protocol ``ChatClient`` (``backend/llm/base.py``), consumido
diretamente pelo motor nativo (``backend/engine/conversation_loop.py``).

Dois invariantes da API do Ollama que o parser respeita:
- `thinking` nunca entra no texto da mensagem — vira `VMessageChunk.
  delta_reasoning`/bloco `reasoning`, concatenar misturaria raciocínio e
  resposta na tela.
- Imagem vai no array `images` em base64 **puro**, sem o prefixo `data:` —
  mandar no formato de content block da OpenAI faz o modelo não ver a
  imagem.

Streaming é NDJSON (não SSE) — um objeto por linha até `done: true`,
já resolvido pelo `OllamaClient.stream_ndjson`.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from backend.llm.ollama.client import OllamaClient, OllamaResponseError
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

_ROLE_POR_TIPO = {
    MessageRole.SYSTEM: "system",
    MessageRole.USER: "user",
    MessageRole.ASSISTANT: "assistant",
    MessageRole.TOOL: "tool",
}

_INVALID: object = object()
_TOOL_CALL_TAG_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)

# Small local models degrade sharply when Ollama receives the complete Vectora
# catalogue (currently close to two hundred schemas).  Keep the tools that
# cover the core agent workflow available, while avoiding the PTY lifecycle
# family and integrations that cannot fit reliably in a small context.  The
# full registry remains the source of truth for execution; this is only a
# request-payload adaptation for oversized Ollama tool payloads.
_COMPACT_TOOL_NAMES = frozenset(
    {
        "terminal",
        "file_read",
        "file_write",
        "file_edit",
        "file_create_dir",
        "file_search",
        "grep",
        "list_dir",
        "workspace_describe",
        "workspace_list",
        "get_workbench_context",
        "browser_start",
        "browser_stop",
        "browser_navigate",
        "browser_snapshot",
        "browser_read_dom",
        "browser_click",
        "browser_fill",
        "browser_scroll",
        "browser_wait_for",
        "browser_list_tabs",
        "browser_select_tab",
        "browser_screenshot",
        "web_search",
        "fetch_url",
        "web_crawl",
        "web_map",
        "write_todos",
        "ask_structured_question",
    }
)
_TOOL_SCHEMA_COMPACT_LIMIT = 48_000
_TOOL_RESULT_COMPACT_LIMIT = 12_000


def _is_retryable_tool_call_error(error: OllamaResponseError) -> bool:
    """Recognize llama-server's transient malformed tool-call response.

    Qwen's chat template can occasionally terminate a generated JSON tool call
    one token early.  Ollama reports this as HTTP 500 before returning any
    assistant content.  Retrying once with deterministic decoding is safe: no
    tool has been executed at this point.
    """
    text = str(error).lower()
    return "invalid tool call arguments" in text or "unexpected end of json" in text


def _payload_tools(
    tools: list[ToolSpec], messages: list[VMessage] | None = None
) -> tuple[list[ToolSpec], bool]:
    """Fit an Ollama tool payload without hiding core local-agent actions.

    Ollama's prompt template in small Qwen models serializes every schema into
    the context.  Sending the complete registry makes the model emit malformed
    or repeated calls before it reaches the user request.  Use the complete
    list below the threshold; above it retain the deterministic core set.
    """
    if not tools:
        return [], False
    try:
        encoded_size = len(
            json.dumps([tool.openai_schema() for tool in tools], ensure_ascii=False)
        )
    except (TypeError, ValueError):
        # A malformed third-party schema must not make the provider unusable;
        # the normal request path will still report the provider error.
        return tools, False
    if encoded_size <= _TOOL_SCHEMA_COMPACT_LIMIT:
        return tools, False
    request_text = " ".join(
        block.text
        for message in (messages or [])
        if message.role == MessageRole.USER
        for block in message.content
        if block.kind == "text" and block.text
    ).lower()
    requested: set[str] = set()
    explicit_names = {tool.name for tool in tools if tool.name.lower() in request_text}
    requested.update(explicit_names)
    if not explicit_names and any(
        word in request_text
        for word in (
            "browser",
            "navegador",
            "página",
            "pagina",
            "site",
            "url",
            "navegue",
            "dom",
        )
    ):
        requested.update(
            {
                "browser_navigate",
                "browser_read_dom",
                "browser_snapshot",
                "browser_click",
                "browser_fill",
                "browser_scroll",
                "browser_wait_for",
                "browser_screenshot",
                "browser_list_tabs",
                "browser_select_tab",
            }
        )
    if not explicit_names and any(
        word in request_text
        for word in (
            "pesquisa",
            "pesquisar",
            "busca",
            "buscar",
            "google",
            "web",
            "internet",
        )
    ):
        requested.update({"web_search", "fetch_url", "web_crawl", "web_map"})
    if not explicit_names and any(
        word in request_text
        for word in (
            "terminal",
            "comando",
            "command",
            "shell",
            "arquivo",
            "pasta",
            "diretório",
            "diretorio",
            "pwd",
            "execut",
            "crie",
        )
    ):
        requested.update(
            {
                "terminal",
                "file_read",
                "file_write",
                "file_edit",
                "file_create_dir",
                "file_search",
                "grep",
                "list_dir",
            }
        )
    compact_names = requested or {"terminal", "web_search", "browser_navigate"}
    compact = [tool for tool in tools if tool.name in compact_names]
    if not compact:
        compact = [tool for tool in tools if tool.name in _COMPACT_TOOL_NAMES]
    return (compact or tools), bool(compact)


def _compact_system_prompt(original: str) -> str:
    """Use a focused identity when a small model needs compact tools.

    The normal Vectora identity is roughly 26k characters in a fresh
    workspace. Together with tool schemas that leaves a small local model
    little room to follow the actual request, so retain the execution and
    safety contract in a short provider-specific form.
    """
    return (
        "Você é a Vectora, uma assistente de engenharia. Responda em português "
        "quando o usuário escrever em português.\n\n"
        "Use uma ferramenta quando ela for necessária e nunca afirme que uma ação "
        "foi executada sem esperar o resultado. Para comandos simples use "
        "diretamente `terminal` com o campo `command`, uma chamada por comando. "
        "Depois do resultado da ferramenta, responda de forma curta e objetiva. "
        "Para pesquisa use `web_search` ou `fetch_url`. Para páginas web use "
        "`browser_navigate`, `browser_snapshot` e as ferramentas de leitura/click.\n\n"
        "O backend aplica o escopo e as permissões do workspace; não tente acessar "
        "fora dele. Se uma ferramenta retornar erro, explique o erro e não repita "
        "a mesma chamada sem alterar os argumentos."
    )


def _compact_tool_results(messages: list[dict]) -> None:
    """Bound huge web/browser results before sending them to a small model."""
    for message in messages:
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if isinstance(content, str) and len(content) > _TOOL_RESULT_COMPACT_LIMIT:
            message["content"] = (
                content[:_TOOL_RESULT_COMPACT_LIMIT]
                + "\n[resultado truncado; não repita a mesma ferramenta, "
                "resuma o que já foi retornado]"
            )


def _split_conteudo(msg: VMessage) -> tuple[str, list[str]]:
    """Devolve `(texto, imagens_base64_puras)` — o prefixo
    `data:image/...;base64,` é removido, o Ollama espera só o payload."""
    texto_partes: list[str] = []
    imagens: list[str] = []
    for bloco in msg.content:
        if bloco.kind == "text" and bloco.text:
            texto_partes.append(bloco.text)
        elif bloco.kind == "image_url" and bloco.image_url:
            bruto = bloco.image_url
            imagens.append(bruto.split(",", 1)[-1] if "," in bruto else bruto)
    return "".join(texto_partes), imagens


def _to_ollama_message(msg: VMessage) -> dict:
    role = _ROLE_POR_TIPO.get(msg.role)
    if role is None:
        erro = f"Mensagem de role {msg.role!r} não tem equivalente no Ollama"
        raise ValueError(erro)

    texto, imagens = _split_conteudo(msg)
    saida: dict[str, Any] = {"role": role, "content": texto}
    if imagens:
        saida["images"] = imagens

    if role == "assistant" and msg.tool_calls:
        saida["tool_calls"] = [
            {"function": {"name": tc.name, "arguments": tc.args}}
            for tc in msg.tool_calls
        ]
    return saida


def _parse_tool_calls(bruto: list) -> list[ToolCall]:
    """`arguments` vem como objeto no Ollama moderno e como string no
    antigo — aceitar os dois evita quebrar contra servidor desatualizado."""
    chamadas: list[ToolCall] = []
    for item in bruto or []:
        funcao = item.get("function") or {}
        args = funcao.get("arguments")
        raw = args if isinstance(args, str) else None
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = _INVALID
        elif args is None:
            args = {}
        chamadas.append(
            ToolCall(
                id=item.get("id") or "",
                name=funcao.get("name", ""),
                args=coerce_json_object(args, raw=raw),
            )
        )
    return chamadas


def _parse_textual_tool_calls(content: str) -> list[ToolCall]:
    """Parse tool calls emitted in Qwen's template text fallback.

    Some Ollama/llama-server combinations return ``<tool_call>...</tool_call>``
    or a double-braced JSON object in ``message.content`` instead of filling
    ``message.tool_calls``.  Accept only an otherwise-empty marker so ordinary
    assistant prose containing JSON is never treated as an invocation.
    """
    stripped = content.strip()
    if not stripped:
        return []
    matches = _TOOL_CALL_TAG_RE.findall(stripped)
    if not matches:
        # Qwen's chat template has emitted both ``{{...}}`` and a variant
        # with one additional closing brace around the JSON object. Try the
        # outer-wrapper removal and a bounded set of trailing-brace trims;
        # never scan arbitrary prose for JSON.
        candidates = [stripped]
        if stripped.startswith("{{"):
            candidates.append(stripped[1:])
        if stripped.startswith("{{"):
            candidates.extend(
                stripped[1:-closing]
                for closing in range(1, 4)
                if len(stripped) > closing and stripped.endswith("}" * closing)
            )
        for candidate in candidates:
            if candidate.startswith("{") and candidate.endswith("}"):
                matches.append(candidate)
                if candidate.endswith("}}"):
                    matches.append(candidate[:-1])
    parsed: list[ToolCall] = []
    seen: set[tuple[str, str]] = set()
    for raw in matches:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict) or not isinstance(value.get("name"), str):
            continue
        args = value.get("arguments", {})
        if not isinstance(args, dict):
            continue
        key = (value["name"], json.dumps(args, sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        parsed.append(
            ToolCall(id=str(value.get("id") or ""), name=value["name"], args=args)
        )
    return parsed


class OllamaChatClient:
    """Chat client nativo do Ollama — implementa o Protocol ``ChatClient``,
    async-only (CLAUDE.md regra 10)."""

    def __init__(self, model: str, client: OllamaClient) -> None:
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
        from backend.settings import settings

        payload_tool_specs, compacted = _payload_tools(tools or [], messages)
        ollama_messages = [_to_ollama_message(m) for m in messages]
        has_tool_result = any(
            message.get("role") == "tool" for message in ollama_messages
        )
        if compacted and ollama_messages and ollama_messages[0]["role"] == "system":
            ollama_messages[0]["content"] = _compact_system_prompt(
                str(ollama_messages[0].get("content") or "")
            )
            _compact_tool_results(ollama_messages)
        if has_tool_result and self.model.lower().startswith("qwen"):
            if ollama_messages and ollama_messages[0]["role"] == "system":
                ollama_messages[0]["content"] += (
                    "\n\nA ferramenta já retornou um resultado nesta rodada. "
                    "Agora responda ao usuário usando esse resultado; não chame "
                    "nenhuma ferramenta novamente, especialmente com os mesmos argumentos."
                )
            # Small Qwen models commonly replay the previous call forever when
            # tool schemas remain present after a result. Force the next turn
            # to be a textual answer; the result is still visible in context.
            payload_tool_specs = []
            ollama_messages.append(
                {
                    "role": "user",
                    "content": "Conclua agora usando o resultado retornado; não use ferramentas.",
                }
            )
        corpo: dict[str, Any] = {
            "model": self.model,
            "messages": ollama_messages,
        }
        predict_limit = max_tokens
        if predict_limit is None:
            predict_limit = (
                min(settings.ollama_num_predict, 256)
                if tools
                else settings.ollama_num_predict
            )
        options: dict[str, Any] = {
            "num_ctx": settings.ollama_num_ctx,
            "num_predict": predict_limit,
        }
        if temperature is not None:
            options["temperature"] = temperature
        elif tools:
            # Tool calling precisa de baixa aleatoriedade em runtimes locais
            # leves, mas temperatura zero faz alguns quantizados encerrarem
            # sem texto nem chamada estruturada. 0.2 mantém o contrato
            # determinístico o bastante sem cair nesse caminho degenerado.
            options["temperature"] = 0.2
        corpo["options"] = options
        if payload_tool_specs:
            corpo["tools"] = [t.openai_schema() for t in payload_tool_specs]
            if (
                compacted
                and corpo["messages"]
                and corpo["messages"][0]["role"] == "system"
            ):
                corpo["messages"][0]["content"] += (
                    "\n\nLocal model guidance: use `terminal` for one-shot shell commands. "
                    "Use the browser tools directly for navigation and reading; "
                    "do not open or manage an interactive PTY."
                )
        return corpo

    @staticmethod
    def _mensagem_da_resposta(corpo: dict) -> dict:
        mensagem = corpo.get("message")
        if not isinstance(mensagem, dict):
            msg = (
                "Ollama respondeu sem `message` — resposta inutilizável "
                f"(model={corpo.get('model')!r})"
            )
            raise OllamaResponseError(msg)
        return mensagem

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
        corpo["stream"] = False
        try:
            resposta = await self.client.post_json("/api/chat", corpo)
        except OllamaResponseError as error:
            if not tools or not _is_retryable_tool_call_error(error):
                raise
            retry = {**corpo, "options": {**corpo["options"], "temperature": 0.0}}
            resposta = await self.client.post_json("/api/chat", retry)
        mensagem = self._mensagem_da_resposta(resposta)

        texto = mensagem.get("content") or ""
        chamadas = _parse_tool_calls(mensagem.get("tool_calls") or [])
        if not chamadas:
            chamadas = _parse_textual_tool_calls(texto)
            if chamadas:
                texto = ""
        content: list[ContentBlock] = []
        raciocinio = mensagem.get("thinking")
        if raciocinio:
            content.append(ContentBlock(kind="reasoning", reasoning_text=raciocinio))
        if texto:
            content.append(ContentBlock(kind="text", text=texto))

        return VMessage(
            role=MessageRole.ASSISTANT,
            content=content,
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
        proximo_index = 0
        texto_pendente: list[str] = []
        has_tool_result = any(message.role == MessageRole.TOOL for message in messages)

        tentativa = 0
        while True:
            try:
                async for evento in self.client.stream_ndjson("/api/chat", corpo):
                    mensagem = evento.get("message") or {}

                    raciocinio = mensagem.get("thinking")
                    if raciocinio:
                        yield VMessageChunk(delta_reasoning=raciocinio)

                    texto = mensagem.get("content")
                    if texto:
                        if tools:
                            texto_pendente.append(texto)
                        else:
                            yield VMessageChunk(delta_text=texto)

                    for chamada in (
                        _parse_tool_calls(mensagem.get("tool_calls") or [])
                        if not has_tool_result
                        else []
                    ):
                        # Ollama não fragmenta argumentos de tool call — chega
                        # sempre completo num único chunk, igual ao Gemini.
                        yield VMessageChunk(
                            tool_call_chunks=[
                                ToolCallChunk(
                                    index=proximo_index,
                                    id=chamada.id or None,
                                    name=chamada.name,
                                    args_fragment=json.dumps(
                                        chamada.args, ensure_ascii=False
                                    ),
                                )
                            ]
                        )
                        proximo_index += 1

                    if evento.get("done"):
                        entrada = int(evento.get("prompt_eval_count") or 0)
                        saida = int(evento.get("eval_count") or 0)
                        if entrada or saida:
                            yield VMessageChunk(
                                usage={
                                    "input_tokens": entrada,
                                    "output_tokens": saida,
                                    "total_tokens": entrada + saida,
                                }
                            )
                break
            except OllamaResponseError as error:
                if tentativa or not tools or not _is_retryable_tool_call_error(error):
                    raise
                tentativa += 1
                corpo = {**corpo, "options": {**corpo["options"], "temperature": 0.0}}
                texto_pendente.clear()

        if tools and texto_pendente:
            texto = "".join(texto_pendente)
            chamadas_textuais = (
                [] if has_tool_result else _parse_textual_tool_calls(texto)
            )
            if chamadas_textuais:
                for chamada in chamadas_textuais:
                    yield VMessageChunk(
                        tool_call_chunks=[
                            ToolCallChunk(
                                index=proximo_index,
                                id=chamada.id or None,
                                name=chamada.name,
                                args_fragment=json.dumps(
                                    chamada.args, ensure_ascii=False
                                ),
                            )
                        ]
                    )
                    proximo_index += 1
            elif has_tool_result and _parse_textual_tool_calls(texto):
                yield VMessageChunk(
                    delta_text="A ferramenta já foi executada; use o resultado retornado para concluir."
                )
            else:
                yield VMessageChunk(delta_text=texto)

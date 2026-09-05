"""Schemas Pydantic da API de chat — single source-of-truth dos contratos.

Usados como request/response models do FastAPI e como tipos internos dos
handlers. Os paths estilo gRPC (`/vectora.chat.v1.ChatService/...`) são
apenas convenção de nomenclatura — não há runtime ConnectRPC nem geração
de stubs protobuf no projeto.
"""

from __future__ import annotations

import base64
import binascii
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class ChatConfig(BaseModel):
    model: str = ""
    llm_provider: str = ""
    recursion_limit: int = 50
    workspace_id: str = ""
    # Sinal explícito de "criar um workspace dedicado pra essa conversa" (modal
    # "Nova conversa" → "criar novo workspace"). Distinto de workspace_id vazio,
    # que hoje significa "sem opinião, reusa o workspace ativo do usuário" —
    # sem esse campo, _resolve_workspace_id não tem como diferenciar as duas
    # intenções e sempre reusa o workspace ativo.
    create_new_workspace: bool = False
    chat_mode: bool = (
        False  # modo Chat: conversacional puro, sem workspace/tools de dev
    )
    custom_system_prompt: str = ""  # instrução personalizada por usuário
    permission_mode: str = "ask"  # ask|accept_edits|plan|auto|bypass
    reasoning_effort: str = ""  # low|medium|high|max (vazio = default do modelo)
    # Idioma preferido do usuário (BCP-47 ou código curto: pt, en, es). Quando
    # vazio, o agente segue a heurística "adapte ao idioma da conversa".
    language: str = ""
    # Fork de checkpoint (editar mensagem / regenerar resposta): checkpoint_id
    # pai da mensagem alvo (ver HistoryMessage.checkpoint_id). Retomar a
    # partir dele ramifica o histórico ali (SessionStore.set_branch_head) —
    # o histórico original continua intacto, só deixa de ser o branch
    # "atual" da thread.
    fork_from_checkpoint_id: str = ""


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


class AttachmentKind(StrEnum):
    """Tipo semântico do attachment — determina como o backend o injeta na mensagem."""

    IMAGE = "image"  # imagem → image_url part (multimodal)
    PDF = "pdf"  # PDF → texto decodificado
    CODE = "code"  # código → bloco de código com linguagem detectada
    TEXT = "text"  # texto genérico → injetado como texto
    AUDIO = "audio"  # áudio → transcrito via STT e injetado como texto


# Espelha frontend/lib/utils/chat/validation.ts::validateImageFile — o
# frontend filtra o picker de arquivo com essas mesmas listas/tetos, mas é só
# UX; sem essa validação aqui, uma chamada direta ao endpoint de stream (sem
# passar pela UI) aceitava qualquer base64 de qualquer tamanho como qualquer
# mimetype.
_ATTACHMENT_MAX_SIZE_DEFAULT_BYTES = 10 * 1024 * 1024
_ATTACHMENT_MAX_SIZE_AUDIO_BYTES = 25 * 1024 * 1024
_ATTACHMENT_MAX_SIZE_HAR_BYTES = 50 * 1024 * 1024

_ATTACHMENT_SUPPORTED_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/gif",
    "image/webp",
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/mp4",
    "audio/m4a",
    "audio/x-m4a",
    "audio/ogg",
    "audio/webm",
    "text/plain",
    "text/markdown",
    "text/x-python",
    "text/x-java",
    "text/x-c",
    "text/x-c++",
    "text/javascript",
    "text/typescript",
    "text/html",
    "text/css",
    "text/xml",
    "application/json",
    "application/javascript",
    "application/typescript",
    "application/x-python",
    "application/x-python-code",
    "application/x-sh",
    "text/x-sh",
    "text/x-log",
    "application/pdf",
}

_ATTACHMENT_SUPPORTED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".cpp",
    ".c",
    ".h",
    ".cs",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".sh",
    ".bash",
    ".yaml",
    ".yml",
    ".json",
    ".xml",
    ".html",
    ".css",
    ".md",
    ".txt",
    ".log",
    ".sql",
    ".graphql",
    ".r",
    ".swift",
    ".kt",
    ".scala",
    ".har",
    ".mp3",
    ".wav",
    ".m4a",
    ".ogg",
    ".webm",
    ".pdf",
}


def _attachment_max_size_bytes(name: str, mime_type: str) -> int:
    lowered = name.lower()
    if lowered.endswith(".har"):
        return _ATTACHMENT_MAX_SIZE_HAR_BYTES
    is_audio = mime_type.startswith("audio/") or any(
        lowered.endswith(ext) for ext in (".mp3", ".wav", ".m4a", ".ogg", ".webm")
    )
    if is_audio:
        return _ATTACHMENT_MAX_SIZE_AUDIO_BYTES
    return _ATTACHMENT_MAX_SIZE_DEFAULT_BYTES


class Attachment(BaseModel):
    """Arquivo anexado a uma mensagem pelo usuário.

    ``base64_data`` armazena o conteúdo em base64 puro (sem prefixo data URL).
    O frontend usa ``fileToBase64()`` que já remove o prefixo ``data:...;base64,``.
    """

    kind: AttachmentKind
    name: str
    mime_type: str
    base64_data: str

    @model_validator(mode="after")
    def _validate_content(self) -> Attachment:
        lowered_name = self.name.lower()
        has_valid_mimetype = self.mime_type in _ATTACHMENT_SUPPORTED_MIME_TYPES
        has_valid_extension = any(
            lowered_name.endswith(ext) for ext in _ATTACHMENT_SUPPORTED_EXTENSIONS
        )
        if not has_valid_mimetype and not has_valid_extension:
            raise ValueError(
                f"Tipo de arquivo não suportado: {self.name!r} ({self.mime_type!r})"
            )

        try:
            decoded = base64.b64decode(self.base64_data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("base64_data inválido") from exc

        max_size = _attachment_max_size_bytes(self.name, self.mime_type)
        if len(decoded) > max_size:
            raise ValueError(
                f"Arquivo {self.name!r} excede o limite de {max_size // (1024 * 1024)}MB"
            )
        return self


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class StreamChatRequest(BaseModel):
    thread_id: str = ""  # vazio → cria nova thread
    content: str
    config: ChatConfig = Field(default_factory=ChatConfig)
    attachments: list[Attachment] = Field(default_factory=list)


class ResumeChatRequest(BaseModel):
    thread_id: str
    interrupt_id: str
    decision: str  # "approve" | "reject" | "edit:<args_json>"


class TranscribeAudioRequest(BaseModel):
    """Dictado de voz gravado no cliente (MediaRecorder) — usado quando a
    Web Speech API do browser não está disponível (Electron/Chromium sem
    a chave de voz do Google embutida)."""

    audio_base64: str
    mime_type: str
    filename: str = "recording.webm"


class TranscribeAudioResponse(BaseModel):
    text: str


class CreateThreadRequest(BaseModel):
    """`workspace_id` vazio deixa o backend criar o workspace dedicado da
    sessão (`~/Documents/vectora/<thread_id>`) na primeira mensagem.

    `mode` vazio mantém o default histórico deste endpoint (`"code"`) —
    caller que sabe a intenção (chat vs código) deve informar explicitamente
    em vez de depender do default."""

    workspace_id: str = ""
    mode: str = ""


class GetThreadRequest(BaseModel):
    thread_id: str


class ListThreadsRequest(BaseModel):
    limit: int = 50
    # Filtro opcional por modo de 1ª classe ("chat"/"code"); vazio = todos.
    mode: str = ""


class DeleteThreadRequest(BaseModel):
    thread_id: str


class UpdateThreadRequest(BaseModel):
    thread_id: str
    title: str | None = None
    pinned: bool | None = None


class GetThreadPinsRequest(BaseModel):
    thread_id: str


class SetThreadPinsRequest(BaseModel):
    thread_id: str
    pins: list[str] = Field(default_factory=list)


class ThreadPinsResponse(BaseModel):
    thread_id: str
    pins: list[str] = Field(default_factory=list)


class GetHistoryRequest(BaseModel):
    thread_id: str


class GenerateTitleRequest(BaseModel):
    thread_id: str


class GenerateTitleResponse(BaseModel):
    title: str


# ---------------------------------------------------------------------------
# Eventos de streaming (oneof StreamChatEvent)
# ---------------------------------------------------------------------------


class ThreadEvent(BaseModel):
    thread_id: str
    # Workspace resolvido pra essa sessão — populado por stream_engine_events a partir
    # do workspace_id já calculado em stream_chat. Frontend usa isso pra
    # sincronizar o seletor de workspace quando um novo workspace é criado
    # (create_new_workspace=True em ChatConfig), já que hoje esse id nunca
    # volta ao cliente por nenhum outro canal.
    workspace_id: str = ""


class TokenEvent(BaseModel):
    content: str
    node: str = ""


class ToolCallEvent(BaseModel):
    tool_name: str
    tool_call_id: str
    args_json: str
    render_hint: str = "json"
    category: str = "general"
    destructive: bool = False
    icon: str = "tool"


class ToolResultEvent(BaseModel):
    tool_call_id: str
    content_json: str
    is_error: bool = False


class NodeEvent(BaseModel):
    node: str
    status: Literal["started", "finished"]
    duration_ms: int = 0
    node_label: str = ""

    @model_validator(mode="after")
    def _fill_node_label(self) -> NodeEvent:
        if not self.node_label and self.node:
            from backend.api.node_labels import get_node_label

            self.node_label = get_node_label(self.node)
        return self


class SubagentOutputEvent(BaseModel):
    """Resultado de uma delegação via ``task()`` — popula o card 'Subagent Outputs'.

    Emitido no início (status='running', content vazio) e no fim (status=
    'complete'/'error', content = resposta do subagente) de cada chamada da
    tool ``task``. ``tool_call_id`` é a chave de dedupe no frontend (mesmo
    run_id do ToolCallEvent), dando identidade — "coder respondeu" / "search
    respondeu" — em vez do bloco genérico "N ações".
    """

    subagent_type: str
    description: str = ""
    status: str = "running"  # "running" | "complete" | "error"
    tool_call_id: str = ""
    content: str = ""
    is_delta: bool = False


class UIMetricsEvent(BaseModel):
    last_node: str = ""
    last_node_ms: int = 0
    rag_hits: int = 0
    rag_misses: int = 0
    tool_calls: dict[str, int] = {}


class HITLEvent(BaseModel):
    tool_name: str
    args_json: str
    interrupt_id: str
    reasoning: str = ""
    affected_paths: list[str] = []
    diff_preview: str = ""
    #: Anotação da aprovação inteligente (avaliador auxiliar/allowlist) —
    #: nunca decide sozinha, só marca a sugestão como reconhecida. O HITL
    #: pausa igual; o humano confirma com um clique a menos.
    pre_approved: bool = False


class RagCitation(BaseModel):
    index: int
    source: str
    chunk: str = ""


class RagCitationEvent(BaseModel):
    """Emitido após busca RAG com a lista de fontes recuperadas.

    O campo ``citations`` expõe cada documento como um item numerado
    (índice 1-based), permitindo ao frontend renderizar referências
    ``[1][2]`` como popovers clicáveis.
    """

    citations: list[RagCitation]


class ErrorEvent(BaseModel):
    message: str
    code: str = "INTERNAL"


class DoneEvent(BaseModel):
    thread_id: str
    run_id: str = ""


class MessageBreakEvent(BaseModel):
    """Sinaliza quebra de bolha: o agente começou a emitir tokens de um nó diferente.

    O frontend cria uma nova mensagem do assistente ao receber este evento.
    """


class WorkbenchInvalidateEvent(BaseModel):
    """Notifica o frontend para recarregar abas específicas do workbench.

    Emitido automaticamente ao fim de tool calls que modificam o workspace.
    ``tabs`` lista quais abas devem ser revalidadas: ``"files"``, ``"diff"``,
    ``"plan"``, ``"background"``.
    """

    tabs: list[str]
    tool_name: str = ""


class ToolActivityEvent(BaseModel):
    """Status da tool em execução — alimenta o AgentStatusLine no frontend.

    Emitido em ``on_tool_start`` (``elapsed_ms=None``) e em ``on_tool_end``
    (``elapsed_ms`` preenchido com a duração em ms). O frontend exibe a
    ferramenta ativa enquanto ``elapsed_ms`` é ``None``; ao receber o evento
    de fim, atualiza a duração e encerra o indicador. ``tool_call_id`` permite
    ao frontend enriquecer o ``ToolCall`` correspondente com o elapsed.
    """

    tool_name: str
    tool_call_id: str = ""
    args_preview: str = ""
    elapsed_ms: int | None = None


class TerminalLineEvent(BaseModel):
    """Linha de output emitida em tempo real pela tool ``terminal``.

    Diferente de ``ToolResultEvent`` (saída completa só ao fim da tool),
    este evento chega incrementalmente enquanto o comando ainda roda —
    o frontend anexa cada linha a um bloco de output ao vivo, associado
    à ``tool_call_id`` da chamada de ``terminal`` em andamento.
    """

    line: str


class ModelSwitchedEvent(BaseModel):
    """Provider trocado automaticamente por quota esgotada (fallback).

    O frontend mostra um toast e atualiza o model selector para o novo modelo.
    Campos ``from_model``/``to_model`` no formato ``"provider:model"``.
    """

    from_model: str
    to_model: str


class TodoItem(BaseModel):
    """Um item da checklist de progresso mantida por ``write_todos``
    (``backend/tools/planning.py``, tool nativa exposta a todo agente)."""

    content: str
    status: Literal["pending", "in_progress", "completed"]


class TodosUpdatedEvent(BaseModel):
    """Emitido quando ``write_todos`` atualiza a checklist de tarefas.

    ``write_todos`` substitui a lista inteira a cada chamada (não é
    incremental) — o payload aqui reflete o snapshot completo mais recente,
    não um delta. Alimenta a seção "Tasks" do Plan tab em tempo real.
    """

    todos: list[TodoItem]


# ---------------------------------------------------------------------------
# Envelope de streaming
# ---------------------------------------------------------------------------

# Cada linha do stream SSE é: data: <StreamChatEvent JSON>
# O campo "type" é o discriminator (equivalente ao oneof do proto).

StreamChatEventPayload = (
    ThreadEvent
    | TokenEvent
    | ToolCallEvent
    | ToolResultEvent
    | NodeEvent
    | UIMetricsEvent
    | HITLEvent
    | SubagentOutputEvent
    | RagCitationEvent
    | ErrorEvent
    | DoneEvent
    | MessageBreakEvent
    | WorkbenchInvalidateEvent
    | ToolActivityEvent
    | ModelSwitchedEvent
    | TerminalLineEvent
    | TodosUpdatedEvent
)

_TYPE_MAP: dict[type, str] = {
    ThreadEvent: "thread",
    TokenEvent: "token",
    ToolCallEvent: "tool_call",
    ToolResultEvent: "tool_result",
    NodeEvent: "node",
    UIMetricsEvent: "ui_metrics",
    HITLEvent: "hitl",
    SubagentOutputEvent: "subagent_output",
    RagCitationEvent: "rag_citations",
    ErrorEvent: "error",
    DoneEvent: "done",
    MessageBreakEvent: "message_break",
    WorkbenchInvalidateEvent: "workbench_invalidate",
    ToolActivityEvent: "tool_activity",
    ModelSwitchedEvent: "model_switched",
    TerminalLineEvent: "terminal_line",
    TodosUpdatedEvent: "todos_updated",
}


def encode_event(payload: StreamChatEventPayload) -> str:
    """Serializa um evento para uma linha SSE: ``data: {...}\\n\\n``."""
    import json

    event_type = _TYPE_MAP[type(payload)]
    data = {"type": event_type, **payload.model_dump()}
    return f"data: {json.dumps(data)}\n\n"


# ---------------------------------------------------------------------------
# Thread management
# ---------------------------------------------------------------------------


class Thread(BaseModel):
    id: str
    created_at: str
    updated_at: str
    title: str = ""
    workspace_id: str = ""
    mode: str = "dev"  # "chat" | "dev" — sessões legadas sem modo são "dev"
    pinned: bool = False


class HistoryMessage(BaseModel):
    role: str
    content: str
    created_at: str = ""
    # Checkpoint pai (estado do thread imediatamente antes desta mensagem
    # existir) — alvo de fork pra "editar e reenviar"/"regenerar". Vazio
    # quando o backend não conseguiu resolver (thread sem checkpointer, erro
    # de leitura do histórico) — ver ChatConfig.fork_from_checkpoint_id.
    checkpoint_id: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class ListThreadsResponse(BaseModel):
    threads: list[Thread]


class GetHistoryResponse(BaseModel):
    messages: list[HistoryMessage]
    # Snapshot mais recente de write_todos (TodoListMiddleware) — repopula a
    # seção "Tasks" do Plan tab num reload de página. Vazio quando a thread
    # nunca chamou write_todos.
    todos: list[TodoItem] = Field(default_factory=list)


class PagedHistoryResponse(BaseModel):
    messages: list[HistoryMessage]
    has_more: bool
    total_count: int


# ---------------------------------------------------------------------------
# Share schemas (leitura pública de threads compartilhadas)
# ---------------------------------------------------------------------------


class CreateShareRequest(BaseModel):
    thread_id: str
    ttl_hours: int = 72


class CreateShareResponse(BaseModel):
    token: str
    url: str
    expires_at: str


class SharedThread(BaseModel):
    thread_id: str
    title: str = ""
    messages: list[HistoryMessage]
    created_at: str
    expires_at: str = ""


# ---------------------------------------------------------------------------
# Tools schema (autodescoberta)
# ---------------------------------------------------------------------------


class ToolSchema(BaseModel):
    name: str
    description: str
    render_hint: str = "json"
    category: str = "general"
    destructive: bool = False
    icon: str = "tool"
    args_schema_json: str = "{}"


class GetToolsResponse(BaseModel):
    tools: list[ToolSchema]


# ---------------------------------------------------------------------------
# Auth schemas
# ---------------------------------------------------------------------------


class SignupRequest(BaseModel):
    email: str
    password: str
    name: str = ""
    # Identidade do app. Vazio → derivado do nome no backend.
    username: str = ""
    invite_token: str = ""


class UsernameAvailableResponse(BaseModel):
    """Disponibilidade de um username para o wizard de criação de conta."""

    # Forma canônica do que foi consultado (minúsculas, sem acento).
    normalized: str
    available: bool
    # Sugestão livre quando o consultado está em uso (ex.: "bruno#4821");
    # igual a `normalized` quando já está livre.
    suggestion: str


class InviteValidationResponse(BaseModel):
    valid: bool
    email: str | None = None
    role: str | None = None


class CreateInviteRequest(BaseModel):
    role: str = "member"
    email: str | None = None
    ttl_hours: int = 24


class InviteInfo(BaseModel):
    token_hash: str
    email: str | None = None
    role: str
    created_by: str | None = None
    expires_at: str
    created_at: str


class CreateInviteResponse(BaseModel):
    token: str
    url: str
    expires_at: str


class InviteListResponse(BaseModel):
    invites: list[InviteInfo]


class SigninRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str = ""


class SignoutRequest(BaseModel):
    refresh_token: str = ""


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class PasswordResetRequestBody(BaseModel):
    email: str


class PasswordResetConfirmBody(BaseModel):
    token: str
    new_password: str


class UserResponse(BaseModel):
    id: str
    username: str = ""
    email: str = ""
    role: str
    name: str = ""
    created_at: str
    last_login_at: str | None = None
    # `exp` (epoch seconds) do access token corrente, repassado pelo
    # middleware via request.state.token_exp. Permite ao frontend agendar um
    # aviso "sessão expira em breve" sem decodificar o JWT (cookie httpOnly —
    # opaco para o JS). `None` quando o middleware não anexou o claim.
    token_expires_at: int | None = None

    @classmethod
    def from_user(cls, user: Any, token_expires_at: int | None = None) -> UserResponse:
        return cls(
            id=user.id,
            username=getattr(user, "username", "") or "",
            email=getattr(user, "email", "") or "",
            role=user.role,
            name=getattr(user, "name", "") or "",
            created_at=user.created_at,
            last_login_at=getattr(user, "last_login_at", None),
            token_expires_at=token_expires_at,
        )


class UpdateProfileRequest(BaseModel):
    """Atualização parcial de perfil — campos opcionais (PATCH /auth/me)."""

    name: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105
    user: UserResponse


class HasUsersResponse(BaseModel):
    exists: bool


class SetupLocalRequest(BaseModel):
    name: str
    company: str = ""
    username: str = ""


class SetupLocalResponse(BaseModel):
    ok: bool


class UserListResponse(BaseModel):
    users: list[UserResponse]


class UpdateRoleRequest(BaseModel):
    role: str


class AuditEntry(BaseModel):
    id: str
    user_id: str | None = None
    action: str
    target_type: str | None = None
    target_id: str | None = None
    timestamp: str
    ip: str = ""
    success: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class EnvOverrideRequest(BaseModel):
    key: str
    value: str

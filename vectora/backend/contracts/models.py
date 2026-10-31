"""Modelos Pydantic para perfis de usuário, settings.json e quality gates.

Contratos canônicos de settings.json versionado e perfis de verificação pós-execução.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# ============================================================================
# Contrato canônico de settings.json (v1)
# ============================================================================


class LocaleSettings(BaseModel):
    language: str = Field(
        default="pt-BR", description="Idioma da interface (ex: pt-BR, en)"
    )
    timezone: str = Field(
        default="America/Sao_Paulo", description="Timezone IANA do usuário"
    )


class AppearanceSettings(BaseModel):
    theme: Literal["dark", "light", "system"] = Field(
        default="dark", description="Tema visual da UI"
    )
    sidebarPosition: Literal["left", "right"] = Field(
        default="left", description="Posição da barra lateral"
    )


class SuggestionsSettings(BaseModel):
    enabled: bool = Field(
        default=True, description="Habilita sugestões de autocompletion"
    )
    aiEnabled: bool = Field(
        default=True, description="Habilita sugestões alimentadas por IA"
    )


class LintSettings(BaseModel):
    enabled: bool = Field(
        default=True, description="Habilita análise estática em tempo real"
    )
    showDiagnostics: bool = Field(
        default=True, description="Exibe diagnósticos inline no editor"
    )


class EditorSettings(BaseModel):
    fontFamily: str = Field(
        default="JetBrains Mono", description="Família da fonte do editor"
    )
    fontSize: int = Field(
        default=13, ge=8, le=32, description="Tamanho da fonte em pixels"
    )
    lineHeight: float = Field(
        default=1.6, ge=1.0, le=3.0, description="Altura da linha"
    )
    fontLigatures: bool = Field(
        default=True, description="Habilita ligaduras tipográficas"
    )
    wordWrap: Literal["off", "on", "wordWrapColumn", "bounded"] = Field(
        default="off", description="Quebra de linha"
    )
    minimap: bool = Field(default=True, description="Exibe minimapa do código")
    smoothScrolling: bool = Field(default=True, description="Rolagem suave")
    formatOnSave: bool = Field(default=True, description="Formatar arquivo ao salvar")
    formatOnPaste: bool = Field(default=False, description="Formatar texto colado")
    suggestions: SuggestionsSettings = Field(default_factory=SuggestionsSettings)
    lint: LintSettings = Field(default_factory=LintSettings)


class WatcherSettings(BaseModel):
    enabled: bool = Field(
        default=True, description="Habilita watcher do sistema de arquivos"
    )
    exclude: list[str] = Field(
        default_factory=list, description="Padrões glob excluídos do monitoramento"
    )


class FileSettings(BaseModel):
    autoSave: Literal["off", "afterDelay", "onFocusChange", "onWindowChange"] = Field(
        default="off", description="Modo de salvamento automático"
    )
    autoSaveDelayMs: int = Field(
        default=1000, ge=100, le=60000, description="Delay em ms para autoSave"
    )
    encoding: str = Field(default="utf-8", description="Codificação padrão de arquivos")
    eol: Literal["lf", "crlf", "auto"] = Field(
        default="lf", description="Terminador de linha"
    )
    trimTrailingWhitespace: bool = Field(
        default=True, description="Remove espaços em branco no final das linhas"
    )
    insertFinalNewline: bool = Field(
        default=True, description="Insere newline no final do arquivo"
    )
    watcher: WatcherSettings = Field(default_factory=WatcherSettings)


class WorkbenchesSettings(BaseModel):
    """Configurações específicas das workbenches.

    O uso de `dict[str, Any]` é justificado pois as seções de cada workbench são
    livres e flexíveis na v1 do schema e poderão ser estritamente tipadas em versões futuras.
    """

    git: dict[str, Any] = Field(
        default_factory=dict, description="Configurações da Git Workbench"
    )
    plan: dict[str, Any] = Field(
        default_factory=dict, description="Configurações do Plan Mode"
    )
    tasks: dict[str, Any] = Field(
        default_factory=dict, description="Configurações de tarefas agendadas"
    )
    browser: dict[str, Any] = Field(
        default_factory=dict, description="Configurações da Browser Workbench"
    )
    storage: dict[str, Any] = Field(
        default_factory=dict, description="Configurações de storage/banco"
    )
    contextGraph: dict[str, Any] = Field(
        default_factory=dict, description="Configurações do Context Graph"
    )
    library: dict[str, Any] = Field(
        default_factory=dict, description="Configurações da biblioteca/docs"
    )
    terminal: dict[str, Any] = Field(
        default_factory=dict, description="Configurações do Terminal integrado"
    )


class UpdatesSettings(BaseModel):
    autoUpdateEnabled: bool = Field(
        default=True, description="Checagem e aplicação de atualizações automáticas"
    )


class UserSettingsV1(BaseModel):
    """Schema do settings.json de usuário (v1)."""

    schema_url: str = Field(
        default="vectora://schemas/settings/v1.json",
        alias="$schema",
        description="URI do schema JSON para autocompletion no Monaco e validadores",
    )
    version: int = Field(default=1, description="Versão do schema do settings.json")
    locale: LocaleSettings = Field(default_factory=LocaleSettings)
    appearance: AppearanceSettings = Field(default_factory=AppearanceSettings)
    editor: EditorSettings = Field(default_factory=EditorSettings)
    files: FileSettings = Field(default_factory=FileSettings)
    workbenches: WorkbenchesSettings = Field(default_factory=WorkbenchesSettings)
    updates: UpdatesSettings = Field(default_factory=UpdatesSettings)


# ============================================================================
# Contratos de Quality Gates e Verificação pós-execução
# ============================================================================

QualityGatePolicy = Literal["block", "degrade", "inform"]

# ============================================================================
# Contratos canônicos do runtime de execução
# ============================================================================

HarnessRunStatus = Literal[
    "planned",
    "admitted",
    "running",
    "waiting_approval",
    "waiting_provider",
    "tool_running",
    "verifying",
    "passed",
    "degraded",
    "failed",
    "cancelled",
    "recovery_required",
]

VerificationOutcome = Literal[
    "passed",
    "failed",
    "infra_error",
    "not_run",
    "blocked",
    "degraded",
    "unverified",
]

MemoryScope = Literal[
    "short_term", "session", "project", "workspace", "user", "vector_rag"
]


class ArtifactRef(BaseModel):
    """Referência imutável a payload persistido fora do transcript quente."""

    artifact_id: str = Field(..., min_length=1)
    media_type: str = Field(default="application/octet-stream", min_length=1)
    byte_size: int = Field(default=0, ge=0)
    sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    storage_uri: str = Field(..., min_length=1)
    retention_class: str = Field(default="run")
    encrypted: bool = False


class CheckpointRef(BaseModel):
    """Estado mínimo necessário para retomar um run sem reprocessar o transcript."""

    checkpoint_id: str = Field(..., min_length=1)
    run_id: str = Field(..., min_length=1)
    thread_id: str = Field(..., min_length=1)
    last_sequence: int = Field(..., ge=0)
    source_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    schema_version: str = Field(..., min_length=1)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)


class ContextProjection(BaseModel):
    """Projeção versionada do contexto; nunca substitui o transcript canônico."""

    projection_id: str = Field(..., min_length=1)
    thread_id: str = Field(..., min_length=1)
    run_id: str = Field(..., min_length=1)
    source_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    covered_from_sequence: int = Field(..., ge=0)
    covered_to_sequence: int = Field(..., ge=0)
    tokens_before: int = Field(default=0, ge=0)
    tokens_after: int = Field(default=0, ge=0)
    preserved_decisions: list[str] = Field(default_factory=list)
    preserved_errors: list[str] = Field(default_factory=list)
    preserved_approvals: list[str] = Field(default_factory=list)
    pending_tool_calls: list[str] = Field(default_factory=list)
    omitted_sequences: list[int] = Field(default_factory=list)


class MemoryRecord(BaseModel):
    """Fato recuperável com escopo e proveniência explícitos."""

    memory_id: str = Field(..., min_length=1)
    scope: MemoryScope
    subject: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    source_run_id: str | None = None
    source_artifact_id: str | None = None
    provenance_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    valid_until: str | None = None
    invalidated_at: str | None = None


class ProviderCapability(BaseModel):
    """Resultado negociado de capacidade; readiness não implica capability."""

    provider: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    discovered: bool = False
    ready: bool = False
    text_generation: bool = False
    streaming: bool = False
    tool_calling: bool = False
    vision: bool = False
    checked_at: str | None = None
    diagnostic: str | None = None


class QualityGateSpec(BaseModel):
    """Definição de um gate de qualidade a ser executado pós-turno do agente."""

    id: str = Field(
        ..., description="Identificador único do gate (ex: pytest, eslint, sast)"
    )
    name: str = Field(..., description="Nome legível para exibição na UI")
    command: list[str] = Field(
        ..., description="Comando e argumentos a serem executados em sandbox"
    )
    policy: QualityGatePolicy = Field(
        default="block",
        description="Comportamento em caso de falha: 'block' (falha o run), 'degrade' (marca degraded), 'inform' (adverte)",
    )
    file_patterns: list[str] = Field(
        default_factory=list,
        description="Padrões glob de arquivos que ativam este gate (ex: ['**/*.py'])",
    )
    timeout_seconds: int = Field(
        default=300, ge=5, le=3600, description="Tempo máximo de execução em segundos"
    )

    @field_validator("command")
    @classmethod
    def validate_command_non_empty(cls, v: list[str]) -> list[str]:
        """Executa a operação validate command non empty."""
        if not v or not any(part.strip() for part in v):
            raise ValueError("command deve conter pelo menos um executável não vazio")
        return v


class WorkspaceVerificationProfile(BaseModel):
    """Perfil de verificação pós-execução configurado por workspace."""

    workspace_id: str = Field(..., description="ID do workspace proprietário do perfil")
    enabled: bool = Field(
        default=True, description="Se a verificação automática está ativa"
    )
    gates: list[QualityGateSpec] = Field(
        default_factory=list, description="Lista de gates configurados"
    )
    require_clean_git: bool = Field(
        default=False, description="Exige árvore de trabalho limpa pós-execução"
    )
    inject_stack_trace_on_failure: bool = Field(
        default=True,
        description="Reinjeta o stack trace / saída de erro no próximo turno para autocorreção",
    )


class QualityGateRunResult(BaseModel):
    """Resultado auditável da execução de um gate de qualidade com evidências associadas."""

    gate_id: str = Field(..., description="ID do gate executado")
    passed: bool = Field(..., description="Indica se o gate foi aprovado")
    exit_code: int = Field(..., description="Código de saída do processo")
    output: str = Field(
        default="", description="Saída textual resumida do comando (stdout/stderr)"
    )
    duration_ms: int = Field(
        default=0, description="Tempo de execução do gate em milissegundos"
    )
    artifacts_collected: list[str] = Field(
        default_factory=list, description="Caminhos de artefatos gerados"
    )

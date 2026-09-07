"""Single Source of Truth for Vectora Configuration.

Pydantic-based settings module that consolidates:
1. Environment variables (3-level hierarchy: defaults.env → .env → ~/.vectora/.env)
2. Application constants (paths, databases, versions)
3. Runtime configuration (debug mode, model selection, logging)

This replaces the scattered config.py, constants.py, and initialization.py.

All configuration is validated at application startup — fails fast with clear
error messages instead of silent NoneType errors mid-execution.
"""

import logging
import os
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import Any, Literal, cast

from dotenv import dotenv_values, load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def _default_vectora_home() -> Path:
    """Resolve o diretório base do Vectora a partir de ``VECTORA_HOME``, se setado.

    Usado como default do campo ``vectora_home`` e pelo bootstrap de
    ``_load_environment_hierarchy`` (roda dentro do ``__init__`` do próprio
    ``Settings``, antes de ``self.vectora_home`` existir na instância — não
    pode depender de ``self.vectora_home`` nem do singleton ``settings``, é o
    próprio singleton sendo construído). Outros módulos que precisam resolver
    o diretório base antes do singleton ``settings`` estar disponível (ex.:
    ``backend/workspace/runtime_settings.py``, importado de dentro deste
    bootstrap) replicam esta mesma leitura direta de ``os.environ`` em vez de
    importar este helper, para não criar import circular com este módulo.
    """
    env_value = os.environ.get("VECTORA_HOME")
    return Path(env_value) if env_value else Path.home() / ".vectora"


class Settings(BaseSettings):
    """Immutable application settings with validation.

    Three-level configuration hierarchy (in order of precedence):
    1. Embedded defaults.env (reproducible, in-package defaults)
    2. Project-local .env (project-specific overrides)
    3. User global ~/.vectora/.env (personal preferences)

    All settings are validated on initialization. Missing required settings
    raise ValidationError immediately instead of causing NoneType errors later.
    """

    # ============================================================================
    # LLM PROVIDER & MODEL CONFIGURATION
    # ============================================================================

    llm_provider: Literal[
        "google-genai",
        "openai",
        "anthropic",
        "ollama",
        "cohere",
        "openrouter",
        "nine_router",
    ] = "google-genai"
    """Active LLM provider (auto-detected from API keys if not set)."""

    # Google Generative AI
    google_api_key: str | None = None
    google_model: str = "gemini-3.6-flash"

    # OpenAI
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o"

    # Anthropic
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"

    # Ollama (local)
    ollama_base_url: str | None = None

    ollama_api_key: str | None = None
    """Chave do Ollama Cloud (ollama.com), não do servidor local.

    Só é necessária pros recursos de nuvem — `web_search`/`web_fetch` e os
    modelos hospedados. O servidor local ignora o header de auth."""
    ollama_model: str = "llama2"

    ollama_num_ctx: int = 32768
    """Janela de contexto (`options.num_ctx`) enviada em toda chamada de chat.

    Sem isso o Ollama usa o default do servidor (variável por VRAM/Modelfile,
    tipicamente bem menor) — o Vectora não tinha controle nenhum sobre o
    tamanho de contexto disponível pro uso agêntico, que exige janela grande.
    """

    ollama_num_predict: int = 4096
    """Teto de tokens de geração (`options.num_predict`) — alto o bastante
    pra não cortar respostas longas do agente."""

    # OpenRouter (proxy multi-provider via API compatível com OpenAI)
    openrouter_api_key: str | None = None
    openrouter_model: str = "openrouter/auto"

    # 9Router (proxy local do usuário, https://github.com/decolua/9router) —
    # integração leve, não absorvida como dependência nativa (diferente de
    # graphify/Sandbox): 3 campos (endpoint + key + modelo), mesmo padrão do
    # OpenRouter, resolvido via ChatOpenAI comum (9Router já fala o protocolo
    # OpenAI completo — sem capacidade extra a justificar um client nativo).
    nine_router_base_url: str | None = None
    nine_router_api_key: str | None = None
    nine_router_default_model: str | None = None

    # Cohere Chat (command-* series)
    # Nota: cohere_api_key (seção EMBEDDINGS abaixo) é reutilizado para ChatCohere.
    # Use LLM_PROVIDER=cohere para habilitar. Não é detectado automaticamente porque
    # COHERE_API_KEY é primariamente para embeddings e reranking.
    cohere_chat_model: str = "command-a-03-2025"

    # ============================================================================
    # APPLICATION IDENTITY & VERSIONING
    # ============================================================================

    version: str = "0.1.0"
    """Vectora version (synced with pyproject.toml)."""

    app_name: str = "Vectora"
    creator_name: str = "Vectora Ltda"

    # ============================================================================
    # RUNTIME BEHAVIOR
    # ============================================================================

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    """Application log level."""

    quiet_mode: bool = False
    """Suppress verbose logs from external libraries (httpx, google-genai, etc.)."""

    # ============================================================================
    # DIRECTORIES (Roaming Profile Pattern)
    # ============================================================================

    vectora_home: Path = Field(default_factory=_default_vectora_home)
    """Base directory for Vectora user data (~/.vectora/), override via VECTORA_HOME."""

    data_dir: Path | None = None
    """Vector store, databases, embeddings (~/.vectora/data/)."""

    logs_dir: Path | None = None
    """Application logs (~/.vectora/logs/)."""

    keys_dir: Path | None = None
    """Sensitive credentials (~/.vectora/keys/)."""

    # ============================================================================
    # STORAGE MODE
    # ============================================================================

    storage_mode: Literal["lite", "complete"] = "lite"
    """Modo de armazenamento.

    lite     — SQLite + LanceDB (padrão, zero dependências externas).
    complete — PostgreSQL + Qdrant + Redis (Pro; requer POSTGRES_DSN,
               QDRANT_URL e REDIS_URL configurados).
    """

    chat_mode: bool = False
    """Modo chat puro: desativa workspace e tools de filesystem no agente.

    Quando True, o frontend oculta WorkspaceSelector e workbench.
    O backend exclui tools de filesystem/workspace do HarnessProfile.
    """

    # ============================================================================
    # DATABASE CONNECTIONS — SQLite (lite)
    # ============================================================================

    db_file: Path | None = None
    """SQLite database for sessions/checkpoints (~/.vectora/data/vectora.db)."""

    embedding_queue_file: Path | None = None
    """SQLite database for embedding queue (~/.vectora/data/embedding_queue.db)."""

    lancedb_dir: str | None = None
    """LanceDB vector store directory (~/.vectora/data/lancedb/)."""

    # Derived connection strings
    db_dsn: str | None = None
    """Database connection string for backend.db (path simples, ver
    ``_initialize_derived_paths`` — usado por kanban/background_tasks/
    agent_profiles/connect, NÃO é checkpoints.db)."""

    embedding_queue_dsn: str | None = None
    """Embedding queue connection string."""

    # ============================================================================
    # DATABASE CONNECTIONS — PostgreSQL (complete)
    # ============================================================================

    postgres_dsn: str | None = (
        "postgresql+asyncpg://vectora:vectora@127.0.0.1:5432/vectora"
    )
    """AsyncPG connection string.

    Default aponta para o docker-compose padrão (postgres:5432, user/pass/db=vectora).
    Usado por checkpointer, store e vector store no modo complete.

    NUNCA usado para usuários/auth/sessões/settings/config — esses dados
    permanecem sempre em SQLite (~/.vectora/checkpoints.db) ou
    JSON/TOML (~/.vectora/settings.json, ~/.vectora/config.toml),
    independentemente de ``storage_mode``. Veja ``src/services/auth.py``
    e ``src/storage/factory.py``.
    """

    # ============================================================================
    # HTTPS / TLS — servidor web (opcional)
    # ============================================================================

    ssl_certfile: str | None = None
    """Caminho do certificado TLS (PEM, fullchain) para servir o web server
    em HTTPS.

    `crypto.randomUUID`, clipboard, service worker e outras APIs do browser
    exigem Secure Context — acessando o Vectora por IP de LAN/Tailscale via
    http:// elas não existem. Com cert + key configurados, o uvicorn sobe
    direto em https://.

    Fontes de certificado comuns:
      - Tailscale: ``tailscale cert <maquina>.<tailnet>.ts.net`` (Let's
        Encrypt automático para o nome da tailnet, sem expor porta).
      - mkcert: certificado local de desenvolvimento confiado na máquina.
      - Let's Encrypt/certbot: para domínio público com porta 80/443.
    """

    ssl_keyfile: str | None = None
    """Caminho da chave privada TLS (PEM) correspondente a ``ssl_certfile``."""

    # ============================================================================
    # REDIS (complete)
    # ============================================================================

    redis_url: str | None = "redis://127.0.0.1:6379/0"
    """URL de conexão Redis.

    Default aponta para o docker-compose padrão (redis:6379/0).
    Usado por KVCache, rate-limit e invalidação pub/sub.
    """

    # ============================================================================
    # QDRANT (complete)
    # ============================================================================

    qdrant_url: str | None = "http://127.0.0.1:6333"
    """Endpoint REST do Qdrant.

    Default aponta para o docker-compose padrão (qdrant:6333).
    Usado como VectorStore alternativo ao LanceDB no modo complete.
    """

    qdrant_api_key: str | None = None
    """API key para Qdrant Cloud. Opcional para instância local."""

    # ============================================================================
    # CONFIG ADMIN — sempre persistido em ~/.vectora/config.toml [server]
    # ============================================================================

    allow_public_signup: bool = False
    """Permite cadastro sem convite. Editável via Admin → Sistema."""

    default_model: str = ""
    """Modelo padrão sugerido a novos usuários. Editável via Admin → Sistema."""

    max_recursion: int = 50
    """Limite de recursão do agente. Editável via Admin → Sistema."""

    # ============================================================================
    # FILE PATHS
    # ============================================================================

    env_file: Path | None = None
    """User configuration file (~/.vectora/.env)."""

    log_file: Path | None = None
    """Main application log file (JSON lines format)."""

    mcp_config_file: Path | None = None
    """MCP server configuration (~/.vectora/mcp.config.json)."""

    chat_config_file: Path | None = None
    """Persistent chat settings (~/.vectora/chat_config.json)."""

    # ============================================================================
    # FEATURE FLAGS & LIMITS
    # ============================================================================

    enable_mcp: bool = False
    """Enable MCP (Model Context Protocol) server integration."""

    enable_features_beta: bool = False
    """Habilita features em desenvolvimento (OAuth, Context Graph, Background Tasks).
    False = oculta features ainda não prontas para produção."""

    max_context_tokens: int = 8000
    """Maximum tokens to keep in message history (sliding window)."""

    max_embedding_queue_size: int = 1000
    """Maximum documents pending embedding before throttling."""

    embedding_batch_size: int = 32
    """Number of documents to embed per batch."""

    # ============================================================================
    # WEB SEARCH (TAVILY)
    # ============================================================================

    tavily_api_key: str | None = None
    """API key for Tavily web search service."""

    # ============================================================================
    # EMBEDDINGS (COHERE) & RAG
    # ============================================================================

    cohere_api_key: str | None = None
    """API key for Cohere embeddings and reranking service."""

    voyage_api_key: str | None = None
    """API key da VoyageAI — alternativa/fallback ao Cohere (embeddings + rerank)."""

    voyage_embedding_model: str = "voyage-3"
    """Modelo de embedding da VoyageAI (hosted, quality-first)."""

    voyage_rerank_model: str = "rerank-2"
    """Modelo de reranking da VoyageAI."""

    embedding_model: str = "embed-multilingual-v3.0"
    """Cohere embedding model. v3.0 multilingual cobre 100+ idiomas (PT-BR)."""

    cohere_calls_per_minute: int = 90
    """Máximo de chamadas de embedding Cohere por minuto.
    Trial keys: 100/min — usamos 90 como buffer de segurança (10% abaixo do limite).
    Production keys: aumentar para 500+ conforme contrato."""

    embedding_dims: int = 1024
    """Cohere v3 retorna vetores de 1024 dimensões."""

    embedding_provider: Literal["cohere", "voyage", "ollama", "openrouter"] | None = (
        None
    )
    """Preferência explícita de provider de embedding. None = fallback padrão
    (Cohere↔Voyage se alguma key configurada; senão Ollama/OpenRouter local)."""

    ollama_embedding_model: str | None = None
    """Modelo de embedding no host Ollama (ex.: "qwen3-embedding:0.6b"). None
    desabilita embeddings via Ollama, mesmo com ollama_base_url configurado —
    o usuário escolhe o modelo explicitamente (sem digitação livre no chat)."""

    openrouter_embedding_model: str | None = None
    """Modelo de embedding via OpenRouter (ex.: "qwen/qwen3-embedding-0.6b").
    None desabilita embeddings via OpenRouter, mesmo com key configurada."""

    # Ollama/OpenRouter são gateways pra qualquer modelo: a capacidade não
    # vem do provider, vem do modelo que o usuário escolheu pra ela. Mesma
    # regra do embedding acima — None significa "essa capacidade não existe
    # aqui", e a tool avisa em vez de adivinhar qual modelo instalado serve.
    ollama_image_model: str | None = None
    """Modelo de geração de imagem no host Ollama. None desabilita."""

    ollama_tts_model: str | None = None
    """Modelo de texto-pra-voz no host Ollama. None desabilita."""

    openrouter_image_model: str | None = None
    """Modelo de geração de imagem via OpenRouter. None desabilita."""

    openrouter_tts_model: str | None = None

    home_assistant_url: str | None = None
    """URL da instância de Home Assistant do usuário (ex.:
    ``http://homeassistant.local:8123``). None desliga as tools de casa."""

    home_assistant_token: str | None = None
    """Long-Lived Access Token do Home Assistant."""

    ollama_video_model: str | None = None
    """Modelo de geração de vídeo no host Ollama. None desabilita."""

    openrouter_video_model: str | None = None
    """Modelo de geração de vídeo via OpenRouter. None desabilita."""

    openrouter_stt_model: str | None = None
    """Modelo de transcrição via OpenRouter (ex.: ``openai/whisper-1``).

    ``None`` desliga a capacidade — `/audio/transcriptions` exige o `model`
    no corpo, e adivinhar um escolheria pelo usuário."""

    openrouter_rerank_model: str | None = None
    """Modelo de rerank via OpenRouter (ex.: ``cohere/rerank-v3.5``).

    ``None`` desliga a capacidade — sem modelo escolhido não há o que chamar
    em ``POST /rerank``."""
    """Modelo de texto-pra-voz via OpenRouter. None desabilita."""

    embedding_queue_enabled: bool = True
    """Enable asynchronous embedding queue processing."""

    # ── Tokenizer / Chunking ─────────────────────────────────────────────────
    tiktoken_encoding: str = "cl100k_base"
    """Tiktoken encoding used for token counting and document chunking.
    cl100k_base é compatível com GPT-4 e serve de boa aproximação para
    Cohere v3 (context de 512 tokens por embedding). Sem HuggingFace."""

    chunk_size: int = 512
    """Tamanho máximo de cada chunk em tokens (para ingestão de documentos)."""

    chunk_overlap: int = 50
    """Sobreposição em tokens entre chunks consecutivos (contexto entre chunks)."""

    default_search_top_k: int = 10
    """Default number of top-k results for vector search."""

    search_min_score: float = 0.5
    """Minimum similarity score threshold for search results."""

    reranker_type: Literal["cohere", "voyage", "openrouter", "none"] = "cohere"
    """Provider de rerank primário; o outro vira secundário no fallback.

    Ollama **não** entra: não expõe endpoint de rerank algum. `Literal` em
    vez de `str` para que um valor fora da lista seja rejeitado na
    validação, não silenciosamente ignorado em `_build_reranker`.
    """

    reranker_model: str = "rerank-multilingual-v3.0"
    """Cohere reranker. v3.0 multilingual ideal para conteúdo PT-BR."""

    reranker_top_k: int = 5
    """Number of results to rerank."""

    # ── RAG Collections & Curadoria Web ───────────────────────────────────────
    rag_collection_default: str = "articles"
    """Coleção LanceDB para docs curados pelo usuário (ingest_docs, embedding manual).
    Conteúdo confiável — escolhido explicitamente pelo usuário."""

    rag_collection_web: str = "web_cache"
    """Coleção LanceDB dedicada a conteúdo vindo da web (cascading automático).
    Separada do bucket curado para audit, observabilidade e purge cirúrgico —
    web é a única superfície de contaminação do RAG."""

    rag_collection_search: str = "search"
    """Coleção LanceDB para conteúdo curado pelo Search Agent.
    Conteúdo indexado pelo search agent após auditoria do RAG — maior confiança
    que web_cache, menor que articles (curado pelo usuário diretamente).
    Ex: fonte canônica fornecida pelo usuário durante uma correção de RAG."""

    web_curation_enabled: bool = True
    """Liga o gate de curadoria (reranker + LLM judge) antes de persistir
    resultados web. Se False, volta ao comportamento legado (indexa tudo)."""

    web_persist_min_score: float = 0.5
    """Score mínimo do reranker para um resultado web sobreviver ao gate de
    curadoria. Abaixo disso é descartado antes mesmo do LLM judge."""

    # ── RAG Curator ────────────────────────────────────────────────────────────
    rag_curator_enabled: bool = True
    """Liga o curator de RAG (resumo automático do manifest após ingestão)."""

    rag_curator_debounce_seconds: float = 30.0
    """Segundos de debounce após último doc indexado antes de disparar o curator.
    Garante que batchs grandes geram apenas 1 LLM call de síntese, não N."""

    # ── Hybrid RAG — BM25 + Dense ─────────────────────────────────────────────
    rag_hybrid_enabled: bool = True
    """Ativa busca híbrida: dense (Cohere) + sparse (BM25) com RRF merge.
    Melhora recall em queries curtas ou com termos técnicos exatos."""

    rag_hybrid_fetch_limit: int = 20
    """Candidatos por coleção para BM25 (pool maior que o resultado final).
    BM25 reordena esses candidatos; RRF faz a fusão final."""

    # ── Multi-query retrieval ─────────────────────────────────────────────────
    rag_multi_query_enabled: bool = True
    """Gera N reformulações da query antes de buscar para aumentar o recall."""

    rag_multi_query_n: int = 3
    """Número de variantes da query geradas pelo LLM (inclui a original)."""

    # ── HyDE — Hypothetical Document Embedding ────────────────────────────────
    rag_hyde_enabled: bool = True
    """Ativa HyDE quando score inicial < threshold: gera documento hipotético,
    embeda-o e usa o vetor resultante para uma segunda busca."""

    rag_hyde_threshold: float = 0.5
    """Score abaixo do qual HyDE é ativado — entre _SCORE_LOW (0.4) e _SCORE_HIGH (0.7)."""

    # ── Semantic Memory ────────────────────────────────────────────────────────
    memory_semantic_enabled: bool = True
    """Armazena embeddings das memórias para busca semântica via search_memory."""

    memory_consolidation_require_approval: bool = True
    """Mesma semântica do `[auto_improve] require_approval` do ai-memory:
    quando True, `memory_consolidation.py` propõe a atualização das seções
    (decisions/gotchas/preferences) via artifact HITL em vez de escrever
    direto em disco — o usuário aprova pela mesma tool que já grava fatos
    aprendidos (`apply_memory_consolidation`)."""

    # ── Parallel Agent Execution ──────────────────────────────────────────────
    rag_parallel_agents_enabled: bool = True
    """Permite ao orchestrator disparar múltiplos agentes em paralelo via asyncio.gather."""

    # ============================================================================
    # MCP (MODEL CONTEXT PROTOCOL)
    # ============================================================================

    mcp_server_url: str | None = None
    """URL for MCP server (if using HTTP transport)."""

    mcp_transport_type: str = "stdio"
    """MCP transport type (stdio, http)."""

    mcp_command: str | None = None
    """MCP server command to execute (stdio mode)."""

    mcp_command_args: list[str] | None = None
    """MCP server command arguments."""

    mcp_command_env_vars: list[str] | None = None
    """Nomes de variáveis de ambiente do processo Vectora repassadas ao
    subprocess MCP stdio, além do allowlist mínimo (PATH/HOME/etc.). Só as
    variáveis explicitamente listadas aqui atravessam — nunca `os.environ`
    inteiro."""

    mcp_timeout: int = 30
    """MCP request timeout in seconds."""

    # ============================================================================
    # OBSERVABILIDADE NATIVA (TELEMETRY)
    # ============================================================================

    telemetry_enabled: bool = True
    """Ativa a telemetria nativa de execução do agente (início/fim de turno,
    tool calls, erros não tratados) via logging estruturado."""

    telemetry_output_path: str | None = None
    """Caminho de um arquivo JSONL dedicado para os eventos de telemetria.
    Se None, os eventos propagam para o log JSON padrão
    (``~/.vectora/logs/backend.jsonl``)."""

    # ============================================================================
    # WEBHOOKS — secrets de verificação de assinatura por provider
    # ============================================================================

    github_webhook_secret: str = ""
    """Secret HMAC-SHA256 configurado no GitHub App → Webhook Secret."""

    gitlab_webhook_secret: str = ""
    """Token de verificação configurado nos webhooks do GitLab."""

    linear_webhook_secret: str = ""
    """Secret configurado nos webhooks do Linear."""

    resend_webhook_secret: str = ""
    """Webhook signing secret do Resend (via Svix)."""

    sendgrid_webhook_key: str = ""
    """Chave pública ECDSA base64 para verificar eventos do SendGrid."""

    mailgun_webhook_signing_key: str = ""
    """Chave HMAC-SHA256 para verificar webhooks do Mailgun."""

    # ============================================================================
    # OAUTH — Google
    # ============================================================================

    google_oauth_client_id: str = ""
    """Client ID do Google OAuth App (console.cloud.google.com)."""

    google_oauth_client_secret: str = ""
    """Client secret do Google OAuth App."""

    google_oauth_redirect_uri: str = "http://localhost:8080/auth/google/callback"
    """Redirect URI registrada no Google OAuth App."""

    # ============================================================================
    # OAUTH — GitLab
    # ============================================================================

    gitlab_oauth_client_id: str = ""
    """Application ID do GitLab OAuth (suporta gitlab.com e self-hosted)."""

    gitlab_oauth_client_secret: str = ""
    """Secret do GitLab OAuth App."""

    gitlab_base_url: str = "https://gitlab.com"
    """URL base do GitLab (troque para instância self-hosted quando necessário)."""

    # ============================================================================
    # INTEGRAÇÕES — Linear / Jira / Notion (API Key)
    # ============================================================================

    linear_api_key: str = ""
    """Personal API key do Linear (settings.linear.app/api)."""

    jira_api_token: str = ""
    """API token do Jira (id.atlassian.com/manage-profile/security/api-tokens)."""

    jira_base_url: str = ""
    """URL base do Jira (ex: https://minha-empresa.atlassian.net)."""

    jira_email: str = ""
    """Email da conta Jira (usado na autenticação Basic)."""

    notion_api_key: str = ""
    """Integration token do Notion (notion.so/my-integrations)."""

    # ============================================================================
    # EMAIL PROVIDER (para webhooks de email recebido)
    # ============================================================================

    email_provider: str = ""
    """Provider de email: 'resend', 'sendgrid' ou 'mailgun'. Vazio = desativado."""

    # ============================================================================
    # GATEWAY — túnel público via Cloudflare Workers (*.vectora.chat), ex-relay
    # ============================================================================

    gateway_url: str = "wss://gateway.vectora.chat"
    """URL base do Cloudflare Worker de gateway. Não altere em produção."""

    gateway_enabled: bool = True
    """Inicia o GatewayClient automaticamente quando há integrações OAuth/
    webhook configuradas. Desative com GATEWAY_ENABLED=false para desligar."""

    vectora_app_secret: str = ""
    """Secret fixo por produto — igual em toda instalação, vem de
    `backend/defaults.env` (mesma hierarquia de env vars usada por todo o
    resto de `settings.py`), comparado pelo Worker via `env.VECTORA_APP_SECRET`
    (`services/src/gateway/index.ts::requireAppSecret`) pra autenticar
    `POST /register`. Não é por-usuário nem por-máquina (mesmo padrão de
    client_secret de app desktop nativo — ex. GitHub Desktop — embutido em
    toda cópia distribuída). Vazio desabilita o handshake de registro
    (degrada pro fallback sem gateway, nunca impede o backend de iniciar)."""

    # ============================================================================
    # PYDANTIC CONFIGURATION
    # ============================================================================

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # ============================================================================
    # INITIALIZATION & VALIDATION
    # ============================================================================

    def __init__(self, **data: Any) -> None:
        """Initialize settings with 3-level hierarchy and validation.

        Hierarchy (in precedence order):
        1. Embedded defaults.env
        2. Project-local .env
        3. ~/.vectora/.env
        4. Constructor arguments

        All directories are created if they don't exist.
        Missing required settings raise ValidationError immediately.
        """
        # Load 3-level environment hierarchy BEFORE Pydantic validation
        self._load_environment_hierarchy()

        # Call parent init (validates all fields)
        super().__init__(**data)

        # Initialize derived paths
        self._initialize_directories()
        self._initialize_derived_paths()

        # Auto-detect LLM provider if not explicitly set
        self._detect_llm_provider()

        logger.info(
            "Settings initialized",
            extra={
                "version": self.version,
                "llm_provider": self.llm_provider,
                "storage_mode": self.storage_mode,
            },
        )

    def _load_environment_hierarchy(self) -> None:
        """Load environment variables with 4-level hierarchy.

        Order of precedence (highest to lowest):
        1. .env (project local)              ← overrides everything (API keys, explicit config)
        2. ~/.vectora/.env (user secrets)    ← API keys do usuário (setup wizard)
        3. ~/.vectora/settings.json          ← preferências de runtime (provider ativo, model)
        4. defaults.env (embedded package)   ← valores padrão do Vectora
        5. OS environment (already loaded)   ← variáveis de sistema

        Separação: settings.json armazena preferências não-secretas (provider, model,
        log_level). ~/.vectora/.env guarda segredos (API keys). Projeto .env tem
        prioridade máxima para desenvolvimento local.
        """
        # Variáveis já presentes no processo (Docker/systemd/CI/testes) antes
        # de qualquer .env ser carregado — têm prioridade sobre os arquivos
        # abaixo mesmo quando eles usam override=True entre si, senão um
        # `.env` de projeto esquecido no filesystem vence silenciosamente um
        # env var que o operador/teste setou de propósito.
        _operator_env = dict(os.environ)

        # Level 5 (lowest): Load embedded defaults.env via setdefault
        try:
            defaults_env = resources.files("backend").joinpath("defaults.env")
            defaults_text = defaults_env.read_text(encoding="utf-8")

            for line in defaults_text.split("\n"):
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    if "=" in stripped:
                        key, value = stripped.split("=", 1)
                        os.environ.setdefault(key.strip(), value.strip())
            logger.debug("Loaded defaults.env from package")
        except (FileNotFoundError, TypeError, ModuleNotFoundError, AttributeError):
            logger.debug("defaults.env not found (normal for development)")

        # Level 3: Load runtime settings (tabela app_settings em
        # ~/.vectora/checkpoints.db — RuntimeSettings, backend/workspace/
        # runtime_settings.py). Contém: active_provider, active_model,
        # storage_mode, auth_required — NÃO contém API keys. Import local
        # (não no topo do módulo) porque runtime_settings importa
        # backend.settings dentro de funções, evitando ciclo na inicialização.
        # override=False (setdefault) para que o projeto .env ainda possa
        # sobrescrever.
        try:
            from backend.workspace.runtime_settings import runtime_settings as _rt

            if _provider := _rt.active_provider:
                os.environ.setdefault("LLM_PROVIDER", _provider)
            if (_model := _rt.active_model) and _provider:
                _model_env_map = {
                    "google-genai": "GOOGLE_MODEL",
                    "openai": "OPENAI_MODEL",
                    "anthropic": "ANTHROPIC_MODEL",
                    "ollama": "OLLAMA_MODEL",
                    "cohere": "COHERE_CHAT_MODEL",
                    "openrouter": "OPENROUTER_MODEL",
                    "nine_router": "NINE_ROUTER_MODEL",
                }
                if _env_var := _model_env_map.get(_provider):
                    os.environ.setdefault(_env_var, _model)
            os.environ.setdefault("STORAGE_MODE", _rt.storage_mode)
            logger.debug("Loaded runtime settings from app_settings (SQLite)")
        except Exception as _e:
            logger.debug(f"Could not load runtime settings: {_e}")

        # Level 3b: Load ~/.vectora/config.toml [server] (config admin persistida —
        # allow_public_signup, default_model, max_recursion). Mesmo arquivo onde
        # write_config_section() grava via PATCH /admin/config.
        _config_toml = _default_vectora_home() / "config.toml"
        if _config_toml.exists():
            try:
                import tomllib as _tomllib

                _cfg = _tomllib.loads(_config_toml.read_text(encoding="utf-8"))
                _server_cfg = _cfg.get("server", {})
                if "allow_public_signup" in _server_cfg:
                    os.environ.setdefault(
                        "ALLOW_PUBLIC_SIGNUP", str(_server_cfg["allow_public_signup"])
                    )
                if "default_model" in _server_cfg:
                    os.environ.setdefault(
                        "DEFAULT_MODEL", str(_server_cfg["default_model"])
                    )
                if "max_recursion" in _server_cfg:
                    os.environ.setdefault(
                        "MAX_RECURSION", str(_server_cfg["max_recursion"])
                    )
                logger.debug(f"Loaded server config from {_config_toml}")
            except Exception as _e:
                logger.debug(f"Could not load config.toml [server]: {_e}")

        # Level 2: Load user global ~/.vectora/.env (segredos pessoais do
        # usuário — fonte de verdade para chaves de LLM, ver abaixo).
        user_env = _default_vectora_home() / ".env"
        user_env_values: dict[str, str | None] = {}
        if user_env.exists():
            load_dotenv(user_env, override=False)
            user_env_values = dotenv_values(user_env)
            logger.debug(f"Loaded user .env from {user_env}")

        # Level 1: Load project-local .env — para configs de dev que não
        # são chaves de LLM, tem precedência sobre o global (ex.: rodar de
        # outro diretório via MCP/orchestrator com config diferente).
        # Also check the package/project root directory to support running from other directories (e.g. via MCP/orchestrator)
        package_root_env = Path(__file__).resolve().parent.parent.parent / ".env"
        project_env = Path.cwd() / ".env"

        # Load package root .env first
        if (
            package_root_env.exists()
            and package_root_env.resolve() != project_env.resolve()
        ):
            load_dotenv(package_root_env, override=True)
            logger.debug(f"Loaded package root .env from {package_root_env}")

        # Load project-local .env (precedence over package root if in a different directory)
        if project_env.exists():
            load_dotenv(project_env, override=True)
            logger.debug(f"Loaded project .env from {project_env}")

        # Chaves de LLM são um caso à parte dentro da própria hierarquia de
        # .env: um .env de projeto/repo esquecido no filesystem (git-ignorado,
        # então invisível em git status/PRs) não pode silenciosamente vencer
        # a chave que o usuário configurou de propósito em ~/.vectora/.env —
        # restaura o valor do usuário.
        for _key_name in set(PROVIDER_API_KEY_ENV.values()):
            if _key_name is None:
                continue
            _user_value = user_env_values.get(_key_name)
            if _user_value and os.environ.get(_key_name) != _user_value:
                logger.debug(
                    f"settings: {_key_name} de .env de projeto/cwd "
                    f"sobrescreveu ~/.vectora/.env — restaurando o valor "
                    f"do usuário (fonte de verdade para chaves de LLM)"
                )
                os.environ[_key_name] = _user_value

        # Nenhum .env (defaults/usuário/projeto) pode vencer uma variável que
        # já estava no processo antes desta função rodar — restaura por
        # cima de tudo que foi carregado acima.
        os.environ.update(_operator_env)

    def _initialize_directories(self) -> None:
        """Create all required directories if they don't exist."""
        self.vectora_home.mkdir(parents=True, exist_ok=True)

        # Set directory paths
        self.data_dir = self.vectora_home / "data"
        self.logs_dir = self.vectora_home / "logs"
        self.keys_dir = self.vectora_home / "keys"

        # Create directories
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.keys_dir.mkdir(parents=True, exist_ok=True)

        logger.debug(
            "Directories initialized",
            extra={
                "vectora_home": str(self.vectora_home),
                "data_dir": str(self.data_dir),
            },
        )

    def _initialize_derived_paths(self) -> None:
        """Set all derived path and connection string fields."""
        # data_dir and logs_dir are always set by _initialize_directories before this;
        # cast to Path so the type checker understands the None branch is unreachable here
        data_dir = cast("Path", self.data_dir)
        logs_dir = cast("Path", self.logs_dir)

        # Database files
        self.db_file = data_dir / "backend.db"
        self.embedding_queue_file = data_dir / "embedding_queue.db"

        # Connection strings
        # db_dsn: path simples (aceito por aiosqlite.connect() e Checkpointer)
        self.db_dsn = str(self.db_file)
        # embedding_queue_dsn: URL SQLAlchemy completa com driver async
        # SQLAlchemy create_async_engine() exige formato: dialect+driver:///path
        # Em Windows, paths absolutos começam com letra (C:\...) então usar as_posix()
        # para evitar escape de barras invertidas
        self.embedding_queue_dsn = (
            f"sqlite+aiosqlite:///{self.embedding_queue_file.as_posix()}"
        )

        # Vector store
        self.lancedb_dir = str(data_dir / "lancedb")

        # Configuration files
        self.env_file = self.vectora_home / ".env"
        self.log_file = logs_dir / "backend.jsonl"
        self.mcp_config_file = self.vectora_home / "mcp.config.json"
        self.chat_config_file = self.vectora_home / "chat_config.json"

    def _detect_llm_provider(self) -> None:
        """Auto-detect LLM provider from available API keys.

        Precedence order (if multiple keys present):
        1. anthropic_api_key
        2. openai_api_key
        3. google_api_key
        4. openrouter_api_key
        5. ollama_base_url
        """
        if self.anthropic_api_key:
            self.llm_provider = "anthropic"
        elif self.openai_api_key:
            self.llm_provider = "openai"
        elif self.google_api_key:
            self.llm_provider = "google-genai"
        elif self.openrouter_api_key:
            self.llm_provider = "openrouter"
        elif self.ollama_base_url:
            self.llm_provider = "ollama"

        logger.debug(f"LLM provider auto-detected: {self.llm_provider}")

    # ============================================================================
    # PUBLIC QUERY METHODS
    # ============================================================================

    def get_llm_provider(self) -> str:
        """Get the active LLM provider.

        Returns:
            Provider name: "google-genai", "openai", "anthropic", or "ollama"
        """
        return self.llm_provider

    def get_llm_model(self) -> str:
        """Get the model name for the active provider.

        Returns:
            Model name configured for current provider
        """
        model_map = {
            "google-genai": self.google_model,
            "openai": self.openai_model,
            "anthropic": self.anthropic_model,
            "ollama": self.ollama_model,
            "cohere": self.cohere_chat_model,
            "openrouter": self.openrouter_model,
            "nine_router": self.nine_router_default_model or "",
        }
        return model_map.get(self.llm_provider, self.google_model)

    def get_llm_api_key(self) -> str | None:
        """Get the API key for the active provider.

        Returns:
            API key or None if not configured
        """
        key_map = {
            "google-genai": self.google_api_key,
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "ollama": None,  # Ollama doesn't require API key
            "cohere": self.cohere_api_key,
            "openrouter": self.openrouter_api_key,
            "nine_router": self.nine_router_api_key,
        }
        return key_map.get(self.llm_provider)

    def configured_llm_providers(self) -> list[str]:
        """Providers de LLM com credencial configurada (para o model selector).

        Ollama não exige key — é incluído só quando ``ollama_base_url`` aponta para
        um host. Cohere usa a precedência de ``get_cohere_api_key`` (env inclusa).
        """
        providers: list[str] = []
        if self.google_api_key:
            providers.append("google-genai")
        if self.openai_api_key:
            providers.append("openai")
        if self.anthropic_api_key:
            providers.append("anthropic")
        if self.get_cohere_api_key():
            providers.append("cohere")
        if self.openrouter_api_key:
            providers.append("openrouter")
        if getattr(self, "nine_router_base_url", None) and getattr(
            self, "nine_router_api_key", None
        ):
            providers.append("nine_router")
        if getattr(self, "ollama_base_url", ""):
            providers.append("ollama")
        return providers

    def get_cohere_api_key(self) -> str | None:
        """Get the API key for Cohere embeddings and reranking.

        Precedência:
        1. Settings.cohere_api_key (carregado de .env / ~/.vectora/.env)
        2. Variável de ambiente COHERE_API_KEY (autodetectada pelo SDK também)

        Nota sobre formato das chaves Cohere:
        Chaves modernas do Cohere têm o prefixo "cohere_" (ex: cohere_AbCd1234...).
        O dashboard do Cohere tem um bug de UI onde ao VISUALIZAR uma chave já criada
        ele exibe apenas os dígitos sem o prefixo "cohere_". Sempre crie uma NOVA chave
        para copiar o valor completo — nunca use o campo "View" de chaves existentes.

        Returns:
            Cohere API key ou None se não configurado.
        """
        return self.cohere_api_key or os.getenv("COHERE_API_KEY")

    def get_available_providers(self) -> list[str]:
        """Get list of providers with API keys configured.

        Returns:
            List of available provider names
        """
        available = []
        if self.anthropic_api_key:
            available.append("anthropic")
        if self.openai_api_key:
            available.append("openai")
        if self.google_api_key:
            available.append("google-genai")
        if self.ollama_base_url:
            available.append("ollama")
        if self.cohere_api_key:
            available.append("cohere")
        if self.openrouter_api_key:
            available.append("openrouter")
        return available

    def set_model(self, provider: str, model: str) -> None:
        """Update model for a specific provider.

        Args:
            provider: LLM provider ("google-genai", "openai", "anthropic", "ollama",
                "cohere", "openrouter")
            model: Model name

        Raises:
            ValueError: If provider is unknown
        """
        if provider not in [
            "google-genai",
            "openai",
            "anthropic",
            "ollama",
            "cohere",
            "openrouter",
            "nine_router",
        ]:
            raise ValueError(f"Unknown LLM provider: {provider}")

        if provider == "google-genai":
            self.google_model = model
        elif provider == "openai":
            self.openai_model = model
        elif provider == "anthropic":
            self.anthropic_model = model
        elif provider == "ollama":
            self.ollama_model = model
        elif provider == "cohere":
            self.cohere_chat_model = model
        elif provider == "openrouter":
            self.openrouter_model = model
        elif provider == "nine_router":
            self.nine_router_default_model = model

        logger.info(f"Model updated: {provider}={model}")


# ============================================================================
# PROVIDER REGISTRY
# ============================================================================
# Catálogo estático de providers/modelos suportados, env vars associadas e
# metadados de display. Consumido pelo TUI (`src/ui/app.py`), pelo setup
# wizard e pela orquestração de troca de modelo em
# `src/services/runtime_settings.py::apply_model_change`.

AVAILABLE_MODELS: dict[str, list[str]] = {
    "google-genai": [
        # Gemini 3.x — geração atual
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite",
        # Gemini 2.5 — bloqueado pro Google pra API keys novas desde
        # jul/2026 ("no longer available to new users"); mantido na lista
        # só pra quem já tinha acesso concedido antes do bloqueio.
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ],
    "openai": [
        # GPT-5.5 — geração atual (frontier)
        "gpt-5.5",
        "gpt-5.5-pro",
        # GPT-5.4
        "gpt-5.4",
        "gpt-5.4-pro",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        # GPT-5
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        # Geração anterior ainda suportada
        "gpt-4.1",
        # Série de raciocínio
        "o3",
        "o4-mini",
    ],
    "anthropic": [
        # Geração mais recente
        "claude-fable-5",
        # Claude 4 — geração atual
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ],
    "cohere": [
        "command-a-plus-05-2026",
        "command-a-03-2025",
        "command-r-plus-08-2024",
        "command-r-08-2024",
        "command-r7b-12-2024",
    ],
}

# Providers cujo modelo aceita imagem na mensagem (Cohere e Ollama não
# suportam multimodal nos clients nativos, independente do modelo
# específico escolhido dentro do provider). Usado por
# `api/handlers/chat.py::stream_chat` para recusar attachments de imagem
# cedo, com um erro claro, em vez de deixar a API do provider estourar com
# uma mensagem crua (ex.: Cohere "image content is not supported").
VISION_CAPABLE_PROVIDERS: set[str] = {"google-genai", "openai", "anthropic"}


class CapabilityState(StrEnum):
    """Knowledge state for a model/provider capability.

    ``UNKNOWN`` is deliberately distinct from ``UNSUPPORTED``: an active
    model may be attempted when metadata is unavailable, while an unknown
    model must never be selected as a multimodal fallback.
    """

    SUPPORTED = "known_capable"
    UNSUPPORTED = "known_incapable"
    UNKNOWN = "unknown"


# Subconjunto de `VISION_CAPABLE_PROVIDERS`: ler vídeo é bem mais restrito
# que ler imagem. OpenAI e Anthropic aceitam imagem na mensagem e recusam
# vídeo, então herdar a lista acima faria `analyze_video` prometer uma
# capacidade que a API rejeita na chamada — o erro apareceria como falha
# crua do provider em vez de aviso claro antes de gastar upload.
VIDEO_INPUT_PROVIDERS: set[str] = {"google-genai"}

# Capacidades multimodais por provider, além do chat (`llm`) e da visão já
# coberta acima. Usado pelas tools de mídia (`backend/tools/media.py`) para
# recusar cedo, com erro claro, o que o provider selecionado não faz — nunca
# trocar de provider por conta própria: o usuário escolheu aquele modelo, se
# ele não gera imagem o certo é avisar, não gerar em outro lugar e cobrar
# uma API que ele não pediu.
#
# Ollama/OpenRouter ficam de fora do dicionário de propósito: são gateways
# pra qualquer modelo, então a capacidade depende do que o usuário
# configurou (`ollama_image_model` etc.), não do provider em si — ver
# `provider_supports`.
PROVIDER_CAPABILITIES: dict[str, set[str]] = {
    "google-genai": {"llm", "vision", "image", "tts", "embedding", "video"},
    "openai": {"llm", "vision", "image", "tts", "embedding"},
    "anthropic": {"llm", "vision"},
    "cohere": {"llm", "embedding", "reranker"},
}

#: Providers-gateway: capacidade resolvida pelo modelo configurado, não por
#: uma lista fixa (ver `provider_supports`).
_GATEWAY_PROVIDERS: frozenset[str] = frozenset({"ollama", "openrouter"})


def provider_capability_state(provider: str, capability: str) -> CapabilityState:
    """Return the tri-state capability contract for a provider.

    Gateway providers are ``UNKNOWN`` at provider level because their model,
    rather than the gateway itself, declares capabilities.
    """
    if provider in _GATEWAY_PROVIDERS:
        return (
            CapabilityState.SUPPORTED
            if configured_gateway_model(provider, capability)
            else CapabilityState.UNKNOWN
        )
    if provider not in PROVIDER_CAPABILITIES:
        return CapabilityState.UNKNOWN
    return (
        CapabilityState.SUPPORTED
        if capability in PROVIDER_CAPABILITIES[provider]
        else CapabilityState.UNSUPPORTED
    )


def provider_supports(provider: str, capability: str) -> bool:
    """Boolean, fail-closed compatibility wrapper for existing media tools.

    For gateway providers, capability depends on the configured model; when
    no model is configured, the wrapper returns ``False``.
    """
    return provider_capability_state(provider, capability) is CapabilityState.SUPPORTED


def configured_gateway_model(provider: str, capability: str) -> str:
    """Modelo escolhido para `provider`+`capability`, ou string vazia.

    Precedência: o que o usuário escolheu na UI (persistido em
    `runtime_settings`) vence a env var. Sem essa ordem, quem configurou por
    env nunca conseguiria trocar de modelo pela interface — a env sempre
    ganharia e a UI pareceria não salvar.
    """
    key = f"{provider}_{capability}_model"
    try:
        from backend.workspace.runtime_settings import runtime_settings

        chosen = str(runtime_settings.media_settings.get(key, "") or "").strip()
        if chosen:
            return chosen
    except Exception:
        # runtime_settings indisponível (boot muito cedo, testes isolados) —
        # cair na env é melhor que estourar dentro de uma checagem de
        # capacidade.
        pass

    from backend.settings import settings as _settings

    return str(getattr(_settings, key, "") or "").strip()


# Modelos que rejeitam REPLAY de tool_calls no histórico da conversa — não é
# sobre suportar tool calling na primeira chamada, é sobre reprocessar um
# turno anterior que já usou tools. Confirmado em produção: `command-a-plus-05-2026`
# devolve 400 "tool plan` cannot be used with this model" porque o client
# nativo sempre serializa `tool_plan` ao converter uma mensagem de
# assistente com tool_calls (ver `backend/llm/provider_fallback.py::
# _PROVIDER_INCOMPATIBLE_MARKERS` pro detalhe reativo — isso aqui é o
# catálogo estático usado para nem oferecer o modelo no code mode, que
# sempre usa tools). Escondido só no code
# mode (`ALL_TOOLS`); no chat mode (`CHAT_TOOLS`) o modelo continua disponível
# — decisão de produto, o chat mode também usa algumas tools mas o usuário
# aceita o risco menor em troca de manter a opção.
TOOL_CALLING_INCOMPATIBLE_MODELS: set[str] = {"cohere:command-a-plus-05-2026"}

# Fontes públicas das janelas de contexto:
#   Gemini: https://ai.google.dev/gemini-api/docs/models
#   Claude: https://platform.claude.com/docs/en/about-claude/models/overview
#   OpenAI: https://developers.openai.com/api/docs/models/all
#   Cohere: https://docs.cohere.com/docs/{command-a-plus,command-r7b}
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # Gemini — toda família roda em 1M
    "gemini-3.6-flash": 1_000_000,
    "gemini-3.5-flash": 1_000_000,
    "gemini-3.1-pro-preview": 1_000_000,
    "gemini-3-flash-preview": 1_000_000,
    "gemini-3.1-flash-lite": 1_000_000,
    "gemini-2.5-flash": 1_000_000,
    "gemini-2.5-pro": 1_000_000,
    # OpenAI — GPT-4.1 inflou para 1M; GPT-5.x e raciocínio ficaram em 200k+
    "gpt-5.5": 400_000,
    "gpt-5.5-pro": 400_000,
    "gpt-5.4": 400_000,
    "gpt-5.4-pro": 400_000,
    "gpt-5.4-mini": 400_000,
    "gpt-5.4-nano": 400_000,
    "gpt-5": 400_000,
    "gpt-5-mini": 400_000,
    "gpt-5-nano": 400_000,
    "gpt-4.1": 1_000_000,
    "o3": 200_000,
    "o4-mini": 200_000,
    # Anthropic — Claude Fable 5 e família Claude 4, todos 200k
    "claude-fable-5": 200_000,
    "claude-opus-4-7": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5": 200_000,
    # Cohere — Command A+ tem input de 128k (menor que o Command A "clássico",
    # apesar do nome — confirmado em cohere.com/blog/command-a-plus); Command
    # A está em 256k; Command R em 128k
    "command-a-plus-05-2026": 128_000,
    "command-a-03-2025": 256_000,
    "command-r-plus-08-2024": 128_000,
    "command-r-08-2024": 128_000,
    "command-r7b-12-2024": 128_000,
}

# Fallback por família para modelos novos ainda não listados em
# MODEL_CONTEXT_WINDOWS. Ordem importa — prefixo mais específico antes.
_FAMILY_CONTEXT_FALLBACKS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("gemini-",), 1_000_000),
    (("gpt-4.1",), 1_000_000),
    (("gpt-", "o3", "o4-"), 200_000),
    (("claude-",), 200_000),
    (("command-a",), 256_000),
    (("command-",), 128_000),
)

# Variável de ambiente da API key por provider. `None` = sem chave necessária
# (Ollama roda local).
PROVIDER_API_KEY_ENV: dict[str, str | None] = {
    "google-genai": "GOOGLE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "ollama": None,
    "cohere": "COHERE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "nine_router": "NINE_ROUTER_API_KEY",
    "tavily": "TAVILY_API_KEY",
}

# Variável de ambiente do modelo ativo por provider.
PROVIDER_MODEL_ENV: dict[str, str] = {
    "google-genai": "GOOGLE_MODEL",
    "openai": "OPENAI_MODEL",
    "anthropic": "ANTHROPIC_MODEL",
    "ollama": "OLLAMA_MODEL",
    "cohere": "COHERE_CHAT_MODEL",
    "openrouter": "OPENROUTER_MODEL",
    "nine_router": "NINE_ROUTER_MODEL",
}

# Nome amigável para exibição no TUI / setup wizard.
PROVIDER_DISPLAY: dict[str, str] = {
    "google-genai": "Google Gemini",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "ollama": "Ollama",
    "cohere": "Cohere",
    "openrouter": "OpenRouter",
}

# URL para obtenção de API key (mostrada pelo setup wizard).
PROVIDER_KEY_URL: dict[str, str] = {
    "google-genai": "https://aistudio.google.com/app/apikey",
    "openai": "https://platform.openai.com/api-keys",
    "anthropic": "https://console.anthropic.com/",
    "cohere": "https://dashboard.cohere.com/api-keys",
    "openrouter": "https://openrouter.ai/keys",
}


def get_available_models(provider: str | None = None) -> dict[str, list[str]]:
    """Retorna modelos disponíveis para um provider ou todos."""
    if provider:
        return {provider: AVAILABLE_MODELS.get(provider, [])}
    return AVAILABLE_MODELS


def get_context_window(model: str) -> int:
    """Janela de contexto (tokens) do modelo.

    Olha primeiro a tabela explícita; se não estiver listado, cai num
    fallback por família a partir do prefixo do id. Default final: 128k.
    """
    explicit = MODEL_CONTEXT_WINDOWS.get(model)
    if explicit is not None:
        return explicit
    for prefixes, window in _FAMILY_CONTEXT_FALLBACKS:
        if model.startswith(prefixes):
            return window
    return 128_000


def find_provider_for_model(model: str) -> str | None:
    """Retorna o provider que possui o modelo, ou None se não encontrado."""
    for provider, models in AVAILABLE_MODELS.items():
        if model in models:
            return provider
    return None


def has_api_key(provider: str) -> bool:
    """True se a env var da API key do provider está populada (Ollama → sempre True)."""
    key_env = PROVIDER_API_KEY_ENV.get(provider)
    if key_env is None:
        return True
    return bool(os.environ.get(key_env))


# ============================================================================
# SINGLETON INSTANCE (For gradual migration from old Config)
# ============================================================================

_settings_instance: Settings | None = None


def get_settings() -> Settings:
    """Get or create the global Settings instance.

    This function provides a singleton pattern for backward compatibility
    while encouraging use of dependency injection for new code.

    Returns:
        Global Settings instance
    """
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


# Module-level singleton for convenient access
settings = get_settings()

/** Registro dos modelos disponíveis e dos provedores aceitos pelo seletor. */

// =============================================================================
// Config Storage
// =============================================================================

/** Bump version to force reset of saved user configs */
export const CONFIG_STORAGE = {
  key: "agent-config",
  versionKey: "agent-config-version",
  version: "1.0",
} as const;

// =============================================================================
// Model Registry
// =============================================================================

export interface ModelConfig {
  id: string;
  name: string;
  provider:
    | "google-genai"
    | "openai"
    | "anthropic"
    | "cohere"
    | "ollama"
    | "openrouter"
    | "nine_router"
    | "llamacpp"
    | "dmr";
  description?: string;
}

/**
 * Todos os modelos disponíveis — fonte única da verdade.
 * Formato id: "<provider>:<model>" — espelhado pelo load_llm() do backend.
 */
export const MODELS = {
  // ── Google Gemini 3.x ─────────────────────────────────────────────────────
  "gemini-3.5-flash": {
    id: "google-genai:gemini-3.5-flash",
    name: "Gemini 3.5 Flash",
    provider: "google-genai",
    description: "Geração atual — rápido e capaz (Google)",
  },
  "gemini-3.1-pro-preview": {
    id: "google-genai:gemini-3.1-pro-preview",
    name: "Gemini 3.1 Pro Preview",
    provider: "google-genai",
    description: "Preview avançado (Google)",
  },
  "gemini-3-flash-preview": {
    id: "google-genai:gemini-3-flash-preview",
    name: "Gemini 3 Flash Preview",
    provider: "google-genai",
    description: "Preview rápido (Google)",
  },
  "gemini-3.1-flash-lite": {
    id: "google-genai:gemini-3.1-flash-lite",
    name: "Gemini 3.1 Flash Lite",
    provider: "google-genai",
    description: "Ultra-leve e econômico (Google)",
  },
  // ── Google Gemini 2.5 ─────────────────────────────────────────────────────
  "gemini-2.5-flash": {
    id: "google-genai:gemini-2.5-flash",
    name: "Gemini 2.5 Flash",
    provider: "google-genai",
    description: "Modelo padrão — rápido e capaz (Google)",
  },
  "gemini-2.5-pro": {
    id: "google-genai:gemini-2.5-pro",
    name: "Gemini 2.5 Pro",
    provider: "google-genai",
    description: "Alta capacidade (Google)",
  },
  // ── OpenAI GPT-5.5 ────────────────────────────────────────────────────────
  "gpt-5.5": {
    id: "openai:gpt-5.5",
    name: "GPT-5.5",
    provider: "openai",
    description: "Frontier — geração atual (OpenAI)",
  },
  "gpt-5.5-pro": {
    id: "openai:gpt-5.5-pro",
    name: "GPT-5.5 Pro",
    provider: "openai",
    description: "Frontier Pro (OpenAI)",
  },
  // ── OpenAI GPT-5.4 ────────────────────────────────────────────────────────
  "gpt-5.4": {
    id: "openai:gpt-5.4",
    name: "GPT-5.4",
    provider: "openai",
    description: "Alto desempenho (OpenAI)",
  },
  "gpt-5.4-pro": {
    id: "openai:gpt-5.4-pro",
    name: "GPT-5.4 Pro",
    provider: "openai",
    description: "Alto desempenho Pro (OpenAI)",
  },
  "gpt-5.4-mini": {
    id: "openai:gpt-5.4-mini",
    name: "GPT-5.4 Mini",
    provider: "openai",
    description: "Equilibrado e eficiente (OpenAI)",
  },
  "gpt-5.4-nano": {
    id: "openai:gpt-5.4-nano",
    name: "GPT-5.4 Nano",
    provider: "openai",
    description: "Ultra-leve (OpenAI)",
  },
  // ── OpenAI GPT-5 ──────────────────────────────────────────────────────────
  "gpt-5": {
    id: "openai:gpt-5",
    name: "GPT-5",
    provider: "openai",
    description: "Geração anterior — estável (OpenAI)",
  },
  "gpt-5-mini": {
    id: "openai:gpt-5-mini",
    name: "GPT-5 Mini",
    provider: "openai",
    description: "Eficiente (OpenAI)",
  },
  "gpt-5-nano": {
    id: "openai:gpt-5-nano",
    name: "GPT-5 Nano",
    provider: "openai",
    description: "Ultra-leve (OpenAI)",
  },
  // ── OpenAI GPT-4.1 + raciocínio ───────────────────────────────────────────
  "gpt-4.1": {
    id: "openai:gpt-4.1",
    name: "GPT-4.1",
    provider: "openai",
    description: "Instrução e código (OpenAI)",
  },
  o3: {
    id: "openai:o3",
    name: "o3",
    provider: "openai",
    description: "Raciocínio avançado (OpenAI)",
  },
  "o4-mini": {
    id: "openai:o4-mini",
    name: "o4-mini",
    provider: "openai",
    description: "Raciocínio eficiente (OpenAI)",
  },
  // ── Modelos Anthropic ─────────────────────────────────────────────────────
  "claude-opus-4-7": {
    id: "anthropic:claude-opus-4-7",
    name: "Claude Opus 4.7",
    provider: "anthropic",
    description: "Máxima capacidade (Anthropic)",
  },
  "claude-sonnet-4-6": {
    id: "anthropic:claude-sonnet-4-6",
    name: "Claude Sonnet 4.6",
    provider: "anthropic",
    description: "Equilíbrio velocidade/qualidade (Anthropic)",
  },
  "claude-haiku-4-5": {
    id: "anthropic:claude-haiku-4-5",
    name: "Claude Haiku 4.5",
    provider: "anthropic",
    description: "Ultra-rápido e econômico (Anthropic)",
  },
  // ── Cohere ────────────────────────────────────────────────────────────────
  "command-a-plus-05-2026": {
    id: "cohere:command-a-plus-05-2026",
    name: "Command A+ (Mai 2026)",
    provider: "cohere",
    description: "Geração mais recente, raciocínio agentic (Cohere)",
  },
  "command-a-03-2025": {
    id: "cohere:command-a-03-2025",
    name: "Command A (Mar 2025)",
    provider: "cohere",
    description: "Geração atual (Cohere)",
  },
  "command-r-plus-08-2024": {
    id: "cohere:command-r-plus-08-2024",
    name: "Command R+ (Aug 2024)",
    provider: "cohere",
    description: "Alto desempenho RAG (Cohere)",
  },
  "command-r-08-2024": {
    id: "cohere:command-r-08-2024",
    name: "Command R (Aug 2024)",
    provider: "cohere",
    description: "RAG eficiente (Cohere)",
  },
  "command-r7b-12-2024": {
    id: "cohere:command-r7b-12-2024",
    name: "Command R7B (Dec 2024)",
    provider: "cohere",
    description: "Modelo compacto (Cohere)",
  },
} as const satisfies Record<string, ModelConfig>;

export type ModelKey = keyof typeof MODELS;
export type ModelOption = (typeof MODELS)[ModelKey]["id"];

// =============================================================================
// Agent Registry  (Vectora tem um único agente — o orchestrator)
// =============================================================================

interface AgentConfig {
  id: string;
  name: string;
  shortName: string;
  description?: string;
}

export const AGENTS = {
  vectora: {
    id: "vectora",
    name: "Vectora",
    shortName: "Vectora",
    description: "Agente de propósito geral com RAG nativo",
  },
} as const satisfies Record<string, AgentConfig>;

type AgentKey = keyof typeof AGENTS;
type AgentType = (typeof AGENTS)[AgentKey]["id"];

// =============================================================================
// Deployment
// =============================================================================

interface DeploymentConfig {
  models: ModelKey[];
  agents: AgentKey[];
  defaultModel: ModelKey;
  defaultAgent: AgentKey;
  requiresAuth: boolean;
}

const DEPLOYMENT: DeploymentConfig = {
  models: [
    // Google Gemini 3.x
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
    // Google Gemini 2.5
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    // OpenAI GPT-5.5
    "gpt-5.5",
    "gpt-5.5-pro",
    // OpenAI GPT-5.4
    "gpt-5.4",
    "gpt-5.4-pro",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    // OpenAI GPT-5
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
    // OpenAI outros
    "gpt-4.1",
    "o3",
    "o4-mini",
    // Modelos Anthropic
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    // Cohere
    "command-a-plus-05-2026",
    "command-a-03-2025",
    "command-r-plus-08-2024",
    "command-r-08-2024",
    "command-r7b-12-2024",
  ],
  agents: ["vectora"],
  // Gemini 2.5 é geração anterior — 3 Flash Preview é o fallback atual.
  defaultModel: "gemini-3-flash-preview",
  defaultAgent: "vectora",
  requiresAuth: false,
};

// =============================================================================
// Core Functions
// =============================================================================

export function getDeploymentConfig(): DeploymentConfig {
  return DEPLOYMENT;
}

// =============================================================================
// Model Functions
// =============================================================================

export function getAllowedModels(): ModelOption[] {
  return getDeploymentConfig().models.map((key) => MODELS[key].id);
}

export function getDefaultModel(): ModelOption {
  return MODELS[getDeploymentConfig().defaultModel].id;
}

export function isModelAllowed(modelId: ModelOption): boolean {
  return getAllowedModels().includes(modelId);
}

/** Aceita `string` (não só `ModelOption`) porque modelos dinâmicos de
 * gateways (Ollama/OpenRouter, registrados em runtime pelo usuário) nunca
 * entram no catálogo estático `MODELS` — o fallback abaixo cobre esses ids. */
export function getModelDisplayName(modelId: string): string {
  const model = Object.values(MODELS).find((m) => m.id === modelId);
  if (model) return model.name;
  // "ollama:qwen3:8b" → "qwen3:8b"; "openrouter:openai/gpt-4o" → "openai/gpt-4o"
  // (tag/id crus, mais legíveis que o id completo com prefixo de gateway).
  // Outros ids desconhecidos (fora do catálogo estático) caem no id completo.
  if (modelId.startsWith("ollama:")) return modelId.slice("ollama:".length);
  if (modelId.startsWith("openrouter:"))
    return modelId.slice("openrouter:".length);
  if (modelId.startsWith("nine_router:"))
    return modelId.slice("nine_router:".length);
  if (modelId.startsWith("llamacpp:")) return modelId.slice("llamacpp:".length);
  return modelId;
}

export function getModelProvider(modelId: string): ModelConfig["provider"] {
  const model = Object.values(MODELS).find((m) => m.id === modelId);
  if (model) return model.provider;
  if (modelId.startsWith("ollama:")) return "ollama";
  if (modelId.startsWith("openrouter:")) return "openrouter";
  if (modelId.startsWith("nine_router:")) return "nine_router";
  if (modelId.startsWith("llamacpp:")) return "llamacpp";
  return "google-genai";
}

/**
 * Providers cujo modelo aceita imagem anexada. Espelha
 * `backend/settings.py::VISION_CAPABLE_PROVIDERS` — o client nativo do
 * Cohere não suporta multimodal (nenhum modelo Cohere), independente do
 * que o próprio modelo saiba fazer via API nativa.
 */
const VISION_CAPABLE_PROVIDERS: ReadonlySet<ModelConfig["provider"]> = new Set([
  "google-genai",
  "openai",
  "anthropic",
]);

export function isProviderVisionCapable(
  provider: ModelConfig["provider"],
): boolean {
  return VISION_CAPABLE_PROVIDERS.has(provider);
}

export type ImageCapability = "known_capable" | "known_incapable" | "unknown";

export interface ProviderModelInfo {
  id: string;
  label: string;
  provider: string;
  available: boolean;
  image_capability: ImageCapability;
}

export function isModelImageCapable(model: ProviderModelInfo): boolean {
  return model.available && model.image_capability === "known_capable";
}

/**
 * Janela de contexto em tokens por modelo. Espelha
 * `src/ui/commands/_shared.py::MODEL_CONTEXT_WINDOWS`. Atualizar nos dois
 * lugares ao registrar um modelo novo.
 */
const MODEL_CONTEXT_WINDOWS: Record<string, number> = {
  "google-genai:gemini-3.5-flash": 1_000_000,
  "google-genai:gemini-3.1-pro-preview": 1_000_000,
  "google-genai:gemini-3-flash-preview": 1_000_000,
  "google-genai:gemini-3.1-flash-lite": 1_000_000,
  "google-genai:gemini-2.5-flash": 1_000_000,
  "google-genai:gemini-2.5-pro": 1_000_000,
  "openai:gpt-5.5": 400_000,
  "openai:gpt-5.5-pro": 400_000,
  "openai:gpt-5.4": 400_000,
  "openai:gpt-5.4-pro": 400_000,
  "openai:gpt-5.4-mini": 400_000,
  "openai:gpt-5.4-nano": 400_000,
  "openai:gpt-5": 400_000,
  "openai:gpt-5-mini": 400_000,
  "openai:gpt-5-nano": 400_000,
  "openai:gpt-4.1": 1_000_000,
  "openai:o3": 200_000,
  "openai:o4-mini": 200_000,
  "anthropic:claude-opus-4-7": 200_000,
  "anthropic:claude-sonnet-4-6": 200_000,
  "anthropic:claude-haiku-4-5": 200_000,
  // Command A+ tem input de 128k — menor que o Command A "clássico" apesar
  // do nome (confirmado em cohere.com/blog/command-a-plus).
  "cohere:command-a-plus-05-2026": 128_000,
  "cohere:command-a-03-2025": 256_000,
  "cohere:command-r-plus-08-2024": 128_000,
  "cohere:command-r-08-2024": 128_000,
  "cohere:command-r7b-12-2024": 128_000,
};

export function getContextWindow(modelId: ModelOption): number {
  const explicit = MODEL_CONTEXT_WINDOWS[modelId];
  if (explicit !== undefined) return explicit;
  if (modelId.startsWith("google-genai:")) return 1_000_000;
  if (modelId.startsWith("openai:gpt-4.1")) return 1_000_000;
  if (modelId.startsWith("openai:")) return 200_000;
  if (modelId.startsWith("anthropic:")) return 200_000;
  if (modelId.startsWith("cohere:command-a")) return 256_000;
  if (modelId.startsWith("cohere:")) return 128_000;
  return 128_000;
}

// =============================================================================
// Agent Functions
// =============================================================================

export function getAllowedAgents(): AgentType[] {
  return getDeploymentConfig().agents.map((key) => AGENTS[key].id);
}

export function getDefaultAgent(): AgentType {
  return AGENTS[getDeploymentConfig().defaultAgent].id;
}

export function isAgentAllowed(agentId: AgentType): boolean {
  return getAllowedAgents().includes(agentId);
}

export function getAgentDisplayName(agentId: AgentType): string {
  const agent = Object.values(AGENTS).find((a) => a.id === agentId);
  return agent?.name ?? agentId;
}

export function getAgentShortDisplayName(agentId: AgentType): string {
  const agent = Object.values(AGENTS).find((a) => a.id === agentId);
  return agent?.shortName ?? agentId;
}

// =============================================================================
// Auth Functions
// =============================================================================

export function isAuthRequired(): boolean {
  return getDeploymentConfig().requiresAuth;
}

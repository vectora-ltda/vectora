/**
 * CANONICAL TYPESCRIPT CONTRACTS (AUTO-GENERATED)
 *
 * Fonte da verdade: vectora/backend/contracts/
 * Atualizado via: uv run python -m devtools.contracts export
 * NÃO EDITAR MANUALMENTE.
 */

// ============================================================================
// Errors Matrix (vectora/backend/contracts/errors/matrix.json)
// ============================================================================

export type CanonicalErrorCode =
  | "BAD_REQUEST"
  | "EMPTY_MODEL_RESPONSE"
  | "FORBIDDEN"
  | "INTERNAL"
  | "INTERNAL_ERROR"
  | "MODEL_NOT_ALLOWED"
  | "MODEL_NO_TOOL_CALLING"
  | "MODEL_NO_VISION"
  | "NOT_FOUND"
  | "RATE_LIMITED"
  | "SERVICE_UNAVAILABLE"
  | "UNAUTHORIZED";

export interface CanonicalErrorSpec {
  status_code: number;
  retryable: boolean;
  category: "auth" | "client" | "server";
}

// ============================================================================
// Settings Schema v1 (vectora/backend/contracts/settings/v1.schema.json)
// ============================================================================

export interface LocaleSettings {
  language?: string;
  timezone?: string;
}

export interface AppearanceSettings {
  theme?: "dark" | "light" | "system";
  sidebarPosition?: "left" | "right";
}

export interface SuggestionsSettings {
  enabled?: boolean;
  aiEnabled?: boolean;
}

export interface LintSettings {
  enabled?: boolean;
  showDiagnostics?: boolean;
}

export interface EditorSettings {
  fontFamily?: string;
  fontSize?: number;
  lineHeight?: number;
  fontLigatures?: boolean;
  wordWrap?: "off" | "on" | "wordWrapColumn" | "bounded";
  minimap?: boolean;
  smoothScrolling?: boolean;
  formatOnSave?: boolean;
  formatOnPaste?: boolean;
  suggestions?: SuggestionsSettings;
  lint?: LintSettings;
}

export interface WatcherSettings {
  enabled?: boolean;
  exclude?: string[];
}

export interface FileSettings {
  autoSave?: "off" | "afterDelay" | "onFocusChange" | "onWindowChange";
  autoSaveDelayMs?: number;
  encoding?: string;
  eol?: "lf" | "crlf" | "auto";
  trimTrailingWhitespace?: boolean;
  insertFinalNewline?: boolean;
  watcher?: WatcherSettings;
}

export interface WorkbenchesSettings {
  git?: Record<string, unknown>;
  plan?: Record<string, unknown>;
  tasks?: Record<string, unknown>;
  browser?: Record<string, unknown>;
  storage?: Record<string, unknown>;
  contextGraph?: Record<string, unknown>;
  library?: Record<string, unknown>;
  terminal?: Record<string, unknown>;
}

export interface UpdatesSettings {
  autoUpdateEnabled?: boolean;
}

export interface UserSettingsV1 {
  $schema?: string;
  version?: number;
  locale?: LocaleSettings;
  appearance?: AppearanceSettings;
  editor?: EditorSettings;
  files?: FileSettings;
  workbenches?: WorkbenchesSettings;
  updates?: UpdatesSettings;
}

// ============================================================================
// Quality Gates & Post-Run Verification (vectora/backend/contracts/quality_gates/schema.json)
// ============================================================================

export type QualityGatePolicy = "block" | "degrade" | "inform";

export interface QualityGateSpec {
  id: string;
  name: string;
  command: string[];
  policy?: QualityGatePolicy;
  file_patterns?: string[];
  timeout_seconds?: number;
}

export interface WorkspaceVerificationProfile {
  workspace_id: string;
  enabled?: boolean;
  gates?: QualityGateSpec[];
  require_clean_git?: boolean;
  inject_stack_trace_on_failure?: boolean;
}

export interface QualityGateRunResult {
  gate_id: string;
  passed: boolean;
  exit_code: number;
  output?: string;
  duration_ms?: number;
  reason?: "passed" | "assertion_failed" | "timeout" | "dispatch_error" | null;
  artifacts_collected?: string[];
}

// ============================================================================
// Execution runtime contracts (vectora/backend/contracts/harness/schema.json)
// ============================================================================

export type HarnessRunStatus =
  | "planned"
  | "admitted"
  | "running"
  | "waiting_approval"
  | "waiting_provider"
  | "tool_running"
  | "verifying"
  | "passed"
  | "degraded"
  | "failed"
  | "cancelled"
  | "recovery_required";

export type VerificationOutcome =
  | "passed"
  | "failed"
  | "infra_error"
  | "not_run"
  | "blocked"
  | "degraded"
  | "unverified";

export interface ArtifactRef {
  artifact_id: string;
  media_type?: string;
  byte_size?: number;
  sha256: string;
  storage_uri: string;
  retention_class?: string;
  encrypted?: boolean;
}

export interface CheckpointRef {
  checkpoint_id: string;
  run_id: string;
  thread_id: string;
  last_sequence: number;
  source_hash: string;
  schema_version: string;
  artifact_refs?: ArtifactRef[];
}

export interface ContextProjection {
  projection_id: string;
  thread_id: string;
  run_id: string;
  source_hash: string;
  covered_from_sequence: number;
  covered_to_sequence: number;
  tokens_before?: number;
  tokens_after?: number;
  preserved_decisions?: string[];
  preserved_errors?: string[];
  preserved_approvals?: string[];
  pending_tool_calls?: string[];
  omitted_sequences?: number[];
}

export interface MemoryRecord {
  memory_id: string;
  scope:
    "short_term" | "session" | "project" | "workspace" | "user" | "vector_rag";
  subject: string;
  content: string;
  source_run_id?: string | null;
  source_artifact_id?: string | null;
  provenance_hash: string;
  confidence?: number;
  importance?: number;
  valid_until?: string | null;
  invalidated_at?: string | null;
}

export interface ProviderCapability {
  provider: string;
  model: string;
  discovered?: boolean;
  ready?: boolean;
  text_generation?: boolean;
  streaming?: boolean;
  tool_calling?: boolean;
  vision?: boolean;
  checked_at?: string | null;
  diagnostic?: string | null;
}

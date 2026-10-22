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
  artifacts_collected?: string[];
}

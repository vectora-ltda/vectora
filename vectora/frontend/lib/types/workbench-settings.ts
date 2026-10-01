import type { ComponentType } from "react";

/** Identificadores das workbenches que podem expor configurações. */
export type WorkbenchId =
  | "context_graph"
  | "storage"
  | "tasks"
  | "browser"
  | "git"
  | "terminal"
  | "files";

/** Dono dos dados alterados por uma configuração. */
export type WorkbenchSettingsScope =
  "user" | "workspace" | "session" | "instance";

/** Superfície que está apresentando a configuração. */
export type WorkbenchSettingsPresentation = "workbench" | "settings";
export type WorkbenchSettingsSurfaceMode = "form" | "native-view" | "link";
export type ResolvedSurfaceMode = WorkbenchSettingsSurfaceMode | "unavailable";

export interface WorkbenchSettingsContext {
  threadId: string | null;
  workspaceId: string | null;
  /** Perfil Chromium resolvido para a sessão da Browser Workbench. */
  browserProfileId?: string | null;
  requestOpenNativeSettings?: () => void;
  settingsViewError?: boolean;
  onRetry?: () => void;
  presentation: WorkbenchSettingsPresentation;
}

/** Contrato único para uma seção de configurações de workbench. */
export interface WorkbenchSettingsDescriptor {
  id: string;
  workbench: WorkbenchId;
  title: () => string;
  description?: () => string;
  icon: ComponentType<{ className?: string }>;
  scope: WorkbenchSettingsScope;
  Component: ComponentType<WorkbenchSettingsContext>;
  surface: Record<WorkbenchSettingsPresentation, WorkbenchSettingsSurfaceMode>;
}

import type { ComponentType } from "react";

/** Identificadores das workbenches que podem expor configurações. */
export type WorkbenchId =
  | "context_graph"
  | "storage"
  | "tasks"
  | "browser"
  | "git"
  | "terminal"
  | "files"
  | "plan"
  | "library";

/** Dono dos dados alterados por uma configuração. */
export type WorkbenchSettingsScope =
  "user" | "workspace" | "session" | "instance";

/** Superfície que está apresentando a configuração. */
export type WorkbenchSettingsPresentation = "workbench" | "settings";
export type WorkbenchSettingsSurfaceMode = "form" | "native-view" | "link";
export type ResolvedSurfaceMode = WorkbenchSettingsSurfaceMode | "unavailable";

export interface WorkbenchSettingsSection {
  id: string;
  title: () => string;
  description?: () => string;
  scope?: WorkbenchSettingsScope;
}

export interface WorkbenchSettingsContext {
  threadId: string | null;
  workspaceId: string | null;
  /** Perfil Chromium resolvido para a sessão da Browser Workbench. */
  browserProfileId?: string | null;
  presentation: WorkbenchSettingsPresentation;
  /** Indica se a superfície de workbench está visível. */
  open?: boolean;
  /** Fecha a superfície atual quando uma view nativa solicita fechamento. */
  onRequestClose?: () => void;
}

/** Contrato único para uma seção de configurações de workbench. */
export interface WorkbenchSettingsDescriptor {
  id: string;
  workbench: WorkbenchId;
  title: () => string;
  description?: () => string;
  icon: ComponentType<{ className?: string }>;
  scope: WorkbenchSettingsScope;
  sections: readonly WorkbenchSettingsSection[];
  Component: ComponentType<WorkbenchSettingsContext>;
  surface: Record<WorkbenchSettingsPresentation, WorkbenchSettingsSurfaceMode>;
}

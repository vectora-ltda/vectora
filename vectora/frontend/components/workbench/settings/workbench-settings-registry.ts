import type {
  WorkbenchSettingsDescriptor,
  WorkbenchSettingsCapability,
  WorkbenchId,
} from "@/lib/types/workbench-settings";
import { createElement } from "react";
import type { WorkbenchSettingsContext } from "@/lib/types/workbench-settings";
import { lazyWithRetry } from "@/lib/lazy-with-retry";
import { Settings2 } from "lucide-react";
import { m } from "@/lib/paraglide/messages";
import { BrowserSettingsContent } from "./browser-settings-content";
import { ContextGraphSettingsForm } from "./context-graph-settings-form";
import { TerminalSettings } from "@/components/workbench/terminal/terminal-panel";
import { GitSettingsTab } from "@/components/settings/git-settings-tab";
import { WORKBENCH_TABS } from "@/lib/stores/workbench-store";
import {
  FileSystemSettingsForm,
  LibrarySettingsForm,
  PlanSettingsForm,
  TasksSettingsForm,
} from "./workbench-settings-forms";

const MemorySettings = lazyWithRetry(
  () =>
    import("./memory-settings").then((mod) => ({
      default: mod.MemorySettings,
    })),
  "workbench-memory-settings",
);
function GitWorkbenchSettings(_context: WorkbenchSettingsContext) {
  return createElement(GitSettingsTab);
}

function section(
  id: string,
  title: () => string,
  scope?: WorkbenchSettingsDescriptor["scope"],
) {
  return scope ? ({ id, title, scope } as const) : ({ id, title } as const);
}

function capability(id: string, status: WorkbenchSettingsCapability["status"]) {
  return { id, status } as const;
}

export const contextGraphSettings: WorkbenchSettingsDescriptor = {
  id: "context-graph-settings",
  workbench: "context_graph",
  title: () => m.graph_settings_title(),
  description: () => m.graph_settings_filetypes_help(),
  icon: Settings2,
  scope: "user",
  sections: [section("indexing", () => m.graph_settings_title(), "user")],
  capabilities: [capability("indexing", "available")],
  Component: ContextGraphSettingsForm,
  surface: { workbench: "form", settings: "form" },
};

export const memorySettings: WorkbenchSettingsDescriptor = {
  id: "memory-settings",
  workbench: "storage",
  title: () => m.rag_settings_title(),
  description: () => m.rag_settings_title(),
  icon: Settings2,
  scope: "instance",
  sections: [section("retrieval", () => m.rag_settings_title(), "instance")],
  capabilities: [capability("retrieval", "available")],
  Component: MemorySettings,
  surface: { workbench: "form", settings: "form" },
};

export const browserSettings: WorkbenchSettingsDescriptor = {
  id: "browser-settings",
  workbench: "browser",
  title: () => m.workbench_browser_settings_title(),
  description: () => m.workbench_browser_settings_description(),
  icon: Settings2,
  scope: "user",
  sections: [
    section("profile", () => m.workbench_browser_settings_title(), "user"),
  ],
  capabilities: [
    capability("profile-storage", "available"),
    capability("permissions", "available"),
    capability("downloads", "available"),
    capability("password-manager-ui", "available"),
    capability("cookies", "available"),
    capability("history", "available"),
    capability("popups", "available"),
  ],
  Component: BrowserSettingsContent,
  surface: { workbench: "form", settings: "form" },
};

export const terminalSettings: WorkbenchSettingsDescriptor = {
  id: "terminal-settings",
  workbench: "terminal",
  title: () => m.terminal_title(),
  description: () => m.terminal_sandbox_editor_autosync_hint(),
  icon: Settings2,
  scope: "workspace",
  sections: [
    section("display", () => m.workbench_terminal_display_title(), "user"),
    section("sandbox", () => m.terminal_title(), "workspace"),
  ],
  capabilities: [
    capability("sandbox", "available"),
    capability("terminal-display", "available"),
  ],
  Component: TerminalSettings,
  surface: { workbench: "form", settings: "form" },
};

export const gitSettings: WorkbenchSettingsDescriptor = {
  id: "git-settings",
  workbench: "git",
  title: () => m.settings_category_git(),
  description: () => m.settings_git_description(),
  icon: Settings2,
  scope: "user",
  sections: [section("git", () => m.settings_category_git(), "user")],
  capabilities: [capability("hooks", "available")],
  Component: GitWorkbenchSettings,
  surface: { workbench: "form", settings: "form" },
};

export const filesSettings: WorkbenchSettingsDescriptor = {
  id: "files-settings",
  workbench: "files",
  title: () => m.workbench_tab_files(),
  icon: Settings2,
  scope: "user",
  sections: [section("editor", () => m.workbench_tab_files(), "user")],
  capabilities: [
    capability("monaco-editor", "available"),
    capability("formatter-service", "available"),
    capability("linter-service", "available"),
  ],
  Component: FileSystemSettingsForm,
  surface: { workbench: "form", settings: "form" },
};

export const planSettings: WorkbenchSettingsDescriptor = {
  id: "plan-settings",
  workbench: "plan",
  title: () => m.workbench_tab_plan(),
  icon: Settings2,
  scope: "user",
  sections: [section("plan", () => m.workbench_tab_plan(), "user")],
  capabilities: [capability("plan-view", "available")],
  Component: PlanSettingsForm,
  surface: { workbench: "form", settings: "form" },
};

export const tasksSettings: WorkbenchSettingsDescriptor = {
  id: "tasks-settings",
  workbench: "tasks",
  title: () => m.workbench_tab_tasks(),
  icon: Settings2,
  scope: "user",
  sections: [section("tasks", () => m.workbench_tab_tasks(), "user")],
  capabilities: [
    capability("background-tasks", "available"),
    capability("retry-policy", "available"),
    capability("concurrency-limit", "available"),
  ],
  Component: TasksSettingsForm,
  surface: { workbench: "form", settings: "form" },
};

export const librarySettings: WorkbenchSettingsDescriptor = {
  id: "library-settings",
  workbench: "library",
  title: () => m.workbench_tab_library(),
  icon: Settings2,
  scope: "user",
  sections: [section("catalogs", () => m.workbench_tab_library(), "user")],
  capabilities: [capability("library-catalogs", "available")],
  Component: LibrarySettingsForm,
  surface: { workbench: "form", settings: "form" },
};

const SETTINGS_BY_WORKBENCH: Record<WorkbenchId, WorkbenchSettingsDescriptor> =
  {
    files: filesSettings,
    git: gitSettings,
    plan: planSettings,
    tasks: tasksSettings,
    browser: browserSettings,
    storage: memorySettings,
    context_graph: contextGraphSettings,
    library: librarySettings,
    terminal: terminalSettings,
  };

/** Fonte única dos descriptors registrados pelas workbenches. */
export const WORKBENCH_SETTINGS: readonly WorkbenchSettingsDescriptor[] =
  WORKBENCH_TABS.map((id) => SETTINGS_BY_WORKBENCH[id]);

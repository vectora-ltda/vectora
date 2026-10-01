import type { WorkbenchSettingsDescriptor } from "@/lib/types/workbench-settings";
import { createElement } from "react";
import type { WorkbenchSettingsContext } from "@/lib/types/workbench-settings";
import { lazyWithRetry } from "@/lib/lazy-with-retry";
import { Settings2 } from "lucide-react";
import { m } from "@/lib/paraglide/messages";
import { BrowserSettingsContent } from "./browser-settings-content";
import { ContextGraphSettingsForm } from "./context-graph-settings-form";
import { TerminalSettings } from "@/components/workbench/terminal/terminal-panel";
import { GitSettingsTab } from "@/components/settings/git-settings-tab";

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

export const contextGraphSettings: WorkbenchSettingsDescriptor = {
  id: "context-graph-settings",
  workbench: "context_graph",
  title: () => m.graph_settings_title(),
  description: () => m.graph_settings_filetypes_help(),
  icon: Settings2,
  scope: "user",
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
  Component: MemorySettings,
  surface: { workbench: "form", settings: "form" },
};

export const browserSettings: WorkbenchSettingsDescriptor = {
  id: "browser-settings",
  workbench: "browser",
  title: () => m.workbench_browser_settings_title(),
  description: () => m.workbench_browser_settings_description(),
  icon: Settings2,
  scope: "session",
  Component: BrowserSettingsContent,
  surface: {
    workbench: "native-view",
    settings: "link",
  },
};

export const terminalSettings: WorkbenchSettingsDescriptor = {
  id: "terminal-settings",
  workbench: "terminal",
  title: () => m.terminal_title(),
  description: () => m.terminal_sandbox_editor_autosync_hint(),
  icon: Settings2,
  scope: "workspace",
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
  Component: GitWorkbenchSettings,
  surface: { workbench: "form", settings: "form" },
};

/** Fonte única dos descriptors registrados pelas workbenches. */
export const WORKBENCH_SETTINGS: readonly WorkbenchSettingsDescriptor[] = [
  contextGraphSettings,
  memorySettings,
  browserSettings,
  terminalSettings,
  gitSettings,
] satisfies readonly WorkbenchSettingsDescriptor[];

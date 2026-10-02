"use client";

import { Suspense } from "react";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { m } from "@/lib/paraglide/messages";
import type {
  WorkbenchSettingsContext,
  WorkbenchSettingsCapability,
  WorkbenchSettingsDescriptor,
  ResolvedSurfaceMode,
} from "@/lib/types/workbench-settings";
import {
  getBrowserCapabilityMatrix,
  getBrowserRuntime,
} from "@/lib/browser-capability-matrix";
import { resolveBrowserSurfaceMode } from "@/lib/browser-capabilities";

interface WorkbenchSettingsContentProps {
  descriptor: WorkbenchSettingsDescriptor;
  context: WorkbenchSettingsContext;
}

const SCOPE_LABELS = {
  user: () => m.workbench_settings_scope_user(),
  workspace: () => m.workbench_settings_scope_workspace(),
  session: () => m.workbench_settings_scope_session(),
  instance: () => m.workbench_settings_scope_instance(),
} as const;

function EmptyState({ children }: { children: string }) {
  return (
    <div className="flex min-h-32 items-center justify-center px-4 py-8 text-center text-sm text-muted-foreground">
      {children}
    </div>
  );
}

/** Resolves a declared surface against runtime capabilities without hiding web fallbacks. */
export function resolveWorkbenchSettingsSurfaceMode(
  descriptor: WorkbenchSettingsDescriptor,
  context: WorkbenchSettingsContext,
): ResolvedSurfaceMode {
  const declared = descriptor.surface[context.presentation];
  if (declared !== "native-view") return declared;
  const hasNativeBrowser =
    typeof window !== "undefined" && Boolean(window.vectora?.browserView);
  if (descriptor.id === "browser-settings" && !hasNativeBrowser) {
    return "form";
  }
  return resolveBrowserSurfaceMode(declared, hasNativeBrowser);
}

/** Resolve declared capabilities against the actual desktop/web runtime. */
export function resolveWorkbenchSettingsCapabilities(
  descriptor: WorkbenchSettingsDescriptor,
  hasDesktopBridge: boolean,
): readonly WorkbenchSettingsCapability[] {
  if (descriptor.workbench !== "browser") return descriptor.capabilities;

  const statuses = new Map<string, "available" | "unavailable">(
    getBrowserCapabilityMatrix(getBrowserRuntime(hasDesktopBridge)).map(
      (capability) => [capability.id, capability.status] as const,
    ),
  );
  return descriptor.capabilities.map((capability) => ({
    ...capability,
    status: statuses.get(capability.id) ?? "unavailable",
  }));
}

/** Renderiza o conteúdo de um descriptor com as regras comuns de escopo. */
export function WorkbenchSettingsContent({
  descriptor,
  context,
}: WorkbenchSettingsContentProps) {
  if (descriptor.scope === "workspace" && !context.workspaceId) {
    return <EmptyState>{m.workbench_settings_missing_workspace()}</EmptyState>;
  }

  if (descriptor.scope === "session" && !context.threadId) {
    return <EmptyState>{m.workbench_settings_missing_session()}</EmptyState>;
  }

  const resolvedSurface = resolveWorkbenchSettingsSurfaceMode(
    descriptor,
    context,
  );
  if (resolvedSurface === "unavailable") {
    return <EmptyState>{m.workbench_settings_unavailable()}</EmptyState>;
  }

  const resolvedCapabilities = resolveWorkbenchSettingsCapabilities(
    descriptor,
    typeof window !== "undefined" && Boolean(window.vectora?.browserView),
  );

  const Component = descriptor.Component;
  return (
    <div
      className="flex min-w-0 w-full flex-col gap-3 p-4"
      data-surface-mode={resolvedSurface}
      data-capability-status={resolvedCapabilities
        .map((capability) => `${capability.id}:${capability.status}`)
        .join(",")}
    >
      <div className="flex min-w-0 flex-col gap-1">
        <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          {SCOPE_LABELS[descriptor.scope]()}
        </span>
        {descriptor.scope === "instance" && (
          <p className="text-xs text-amber-600 dark:text-amber-400">
            {m.workbench_settings_instance_warning()}
          </p>
        )}
      </div>
      {descriptor.sections.length > 1 && (
        <nav
          aria-label={m.workbench_settings_sections()}
          className="flex flex-wrap gap-2"
        >
          {descriptor.sections.map((section) => (
            <span
              key={section.id}
              className="rounded border border-border/60 px-2 py-1 text-[10px] text-muted-foreground"
            >
              {section.title()}
              {section.scope ? ` · ${SCOPE_LABELS[section.scope]()}` : ""}
            </span>
          ))}
        </nav>
      )}
      <ErrorBoundary fallbackMessage={m.workbench_settings_error()}>
        <Suspense
          fallback={<EmptyState>{m.workbench_settings_loading()}</EmptyState>}
        >
          <Component {...context} />
        </Suspense>
      </ErrorBoundary>
    </div>
  );
}

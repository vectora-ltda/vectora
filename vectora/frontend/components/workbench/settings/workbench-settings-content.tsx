"use client";

import { Suspense } from "react";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { m } from "@/lib/paraglide/messages";
import type {
  WorkbenchSettingsContext,
  WorkbenchSettingsDescriptor,
} from "@/lib/types/workbench-settings";
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

  const Component = descriptor.Component;
  const mode = resolveBrowserSurfaceMode(
    descriptor.surface[context.presentation],
  );
  if (mode === "unavailable") {
    return (
      <EmptyState>{m.workbench_browser_settings_unavailable()}</EmptyState>
    );
  }
  if (mode === "link") {
    const hasNativeBridge =
      typeof window !== "undefined" && Boolean(window.vectora?.browserView);
    if (!hasNativeBridge) {
      return (
        <EmptyState>{m.workbench_browser_settings_unavailable()}</EmptyState>
      );
    }
    return (
      <div className="flex min-h-32 items-center justify-center px-4 py-8 text-center text-sm text-muted-foreground">
        {context.requestOpenNativeSettings ? (
          <button
            type="button"
            className="rounded border border-border px-3 py-2 text-foreground hover:bg-accent"
            onClick={context.requestOpenNativeSettings}
          >
            {m.workbench_browser_settings_toggle()}
          </button>
        ) : (
          descriptor.title()
        )}
      </div>
    );
  }
  return (
    <div className="flex min-w-0 w-full flex-col gap-3 p-4">
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

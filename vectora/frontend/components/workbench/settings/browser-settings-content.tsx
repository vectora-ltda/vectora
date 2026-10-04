"use client";

import { useRef, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { useSettingsOverlayStore } from "@/lib/stores/settings-overlay-store";
import { useBrowserSettingsController } from "@/lib/stores/browser-settings-controller";
import { useWorkbenchStore } from "@/lib/stores/workbench-store";
import { m } from "@/lib/paraglide/messages";
import type { WorkbenchSettingsContext } from "@/lib/types/workbench-settings";
import { BrowserSettingsForm } from "./browser-settings-form";
import { useBrowserSettingsView } from "../tabs/use-browser-settings-view";

interface NativeBrowserSettingsProps {
  context: WorkbenchSettingsContext;
}

function NativeBrowserSettings({ context }: NativeBrowserSettingsProps) {
  const { browserProfileId: profileId, open, onRequestClose } = context;
  const containerRef = useRef<HTMLDivElement>(null);
  const [retryKey, setRetryKey] = useState(0);
  const { status } = useBrowserSettingsView({
    profileId,
    open: open ?? false,
    containerRef,
    onClose: onRequestClose,
    retryKey,
  });

  if (status === "failed") {
    return (
      <div
        data-testid="browser-settings-form-fallback"
        className="flex h-full min-h-32 flex-col gap-3 overflow-auto p-4"
      >
        <p className="text-sm text-muted-foreground">
          {m.workbench_browser_settings_error()}
        </p>
        <BrowserSettingsForm {...context} />
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded border border-border/60 px-3 py-2 text-sm hover:bg-muted/40"
          onClick={() => setRetryKey((value) => value + 1)}
        >
          <RefreshCw className="h-4 w-4" />
          {m.workbench_browser_settings_toggle()}
        </button>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="relative h-full min-h-32 w-full bg-background"
    >
      {status === "creating" ? (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/80">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : null}
    </div>
  );
}

/** Descriptor entry point for browser settings in both presentations. */
export function BrowserSettingsContent(context: WorkbenchSettingsContext) {
  if (context.presentation === "settings") {
    return (
      <div className="space-y-3 rounded border border-border/60 p-4">
        <p className="text-sm text-muted-foreground">
          {m.workbench_browser_settings_description()}
        </p>
        <button
          type="button"
          className="rounded border border-border/60 px-3 py-2 text-sm hover:bg-muted/40"
          disabled={!context.threadId}
          onClick={() => {
            if (!context.threadId) return;
            useSettingsOverlayStore.getState().setOpen(false);
            useBrowserSettingsController
              .getState()
              .requestOpenNativeSettings(context.threadId);
            useWorkbenchStore.getState().openBrowserSettings(context.threadId);
          }}
        >
          {m.workbench_browser_settings_toggle()}
        </button>
      </div>
    );
  }

  const browserView =
    typeof window !== "undefined" ? window.vectora?.browserView : undefined;
  if (!browserView || !context.browserProfileId) {
    return <BrowserSettingsForm {...context} />;
  }

  return <NativeBrowserSettings context={context} />;
}

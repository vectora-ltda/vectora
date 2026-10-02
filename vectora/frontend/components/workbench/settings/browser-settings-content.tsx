"use client";

import { useRef, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { useSettingsOverlayStore } from "@/lib/stores/settings-overlay-store";
import { useBrowserSettingsController } from "@/lib/stores/browser-settings-controller";
import { useWorkbenchStore } from "@/lib/stores/workbench-store";
import { m } from "@/lib/paraglide/messages";
import type { WorkbenchSettingsContext } from "@/lib/types/workbench-settings";
import { useBrowserSettingsView } from "../tabs/use-browser-settings-view";

interface NativeBrowserSettingsProps {
  profileId: string;
  open: boolean;
  onRequestClose?: () => void;
}

function NativeBrowserSettings({
  profileId,
  open,
  onRequestClose,
}: NativeBrowserSettingsProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [retryKey, setRetryKey] = useState(0);
  const { status } = useBrowserSettingsView({
    profileId,
    open,
    containerRef,
    onClose: onRequestClose,
    retryKey,
  });

  if (status === "failed") {
    return (
      <div className="flex h-full min-h-32 flex-col items-center justify-center gap-3 p-4 text-center">
        <p className="text-sm text-muted-foreground">
          {m.workbench_browser_settings_error()}
        </p>
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
    const hasBrowserBridge =
      typeof window !== "undefined" && Boolean(window.vectora?.browserView);
    if (!hasBrowserBridge) {
      return (
        <p className="text-sm text-muted-foreground">
          {m.workbench_browser_settings_unavailable()}
        </p>
      );
    }
    return (
      <div className="space-y-3 rounded border border-border/60 p-4">
        <p className="text-sm text-muted-foreground">
          {m.workbench_browser_settings_description()}
        </p>
        <button
          type="button"
          className="rounded border border-border/60 px-3 py-2 text-sm hover:bg-muted/40"
          disabled={!context.threadId || !hasBrowserBridge}
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
  if (!browserView) {
    return (
      <p className="text-sm text-muted-foreground">
        {m.workbench_browser_settings_unavailable()}
      </p>
    );
  }
  if (!context.browserProfileId) return null;

  return (
    <NativeBrowserSettings
      profileId={context.browserProfileId}
      open={context.open ?? false}
      onRequestClose={context.onRequestClose}
    />
  );
}

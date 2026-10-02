"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { useSettingsOverlayStore } from "@/lib/stores/settings-overlay-store";
import { useWorkbenchStore } from "@/lib/stores/workbench-store";
import { m } from "@/lib/paraglide/messages";
import type { WorkbenchSettingsContext } from "@/lib/types/workbench-settings";
import { BrowserSettingsForm } from "./browser-settings-form";

interface NativeBrowserSettingsProps {
  profileId: string;
  onRequestClose?: () => void;
}

function NativeBrowserSettings({
  profileId,
  onRequestClose,
}: NativeBrowserSettingsProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [viewId, setViewId] = useState<number | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">(() =>
    typeof window !== "undefined" && window.vectora?.browserView && profileId
      ? "loading"
      : "error",
  );
  const [attempt, setAttempt] = useState(0);
  const browserView =
    typeof window !== "undefined" ? window.vectora?.browserView : undefined;
  const closeRequestRef = useRef(onRequestClose);

  useEffect(() => {
    closeRequestRef.current = onRequestClose;
  }, [onRequestClose]);

  useEffect(() => {
    if (attempt < 0) return;
    if (!browserView || !profileId) {
      return;
    }
    let disposed = false;
    let nativeViewId: number | null = null;
    let unsubscribe: (() => void) | undefined;

    const fail = () => {
      if (disposed) return;
      setState("error");
      if (nativeViewId !== null) {
        browserView.setVisible(nativeViewId, false);
        browserView.destroyView(nativeViewId);
        nativeViewId = null;
        setViewId(null);
      }
    };

    void browserView
      .createView({ profileId, kind: "native-settings" })
      .then((createdViewId) => {
        if (disposed) {
          browserView.destroyView(createdViewId);
          return;
        }
        nativeViewId = createdViewId;
        setViewId(createdViewId);
        unsubscribe = browserView.onEvent((eventViewId, event) => {
          if (eventViewId !== createdViewId) return;
          if (
            event.type === "navigated" &&
            event.url.startsWith("chrome://settings")
          ) {
            setState("ready");
          } else if (event.type === "loadFailed") {
            fail();
          } else if (event.type === "escapePressed") {
            closeRequestRef.current?.();
          }
        });
        return browserView.navigate(createdViewId, "chrome://settings");
      })
      .then((result) => {
        if (result && !result.ok) fail();
      })
      .catch(fail);

    return () => {
      disposed = true;
      unsubscribe?.();
      if (nativeViewId !== null) {
        browserView.setVisible(nativeViewId, false);
        browserView.destroyView(nativeViewId);
      }
    };
  }, [attempt, browserView, profileId]);

  useEffect(() => {
    if (viewId === null || !browserView) return;
    const container = containerRef.current;
    if (!container) return;
    const reportBounds = () => {
      const rect = container.getBoundingClientRect();
      const bounds = {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.max(0, Math.round(rect.width)),
        height: Math.max(0, Math.round(rect.height)),
      };
      browserView.setBounds(viewId, bounds);
      browserView.setVisible(
        viewId,
        state === "ready" && bounds.width > 0 && bounds.height > 0,
      );
    };
    reportBounds();
    const observer =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(reportBounds)
        : undefined;
    observer?.observe(container);
    window.addEventListener("resize", reportBounds);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", reportBounds);
      browserView.setVisible(viewId, false);
    };
  }, [browserView, state, viewId]);

  if (state === "error") {
    return (
      <div className="flex h-full min-h-32 flex-col items-center justify-center gap-3 p-4 text-center">
        <p className="text-sm text-muted-foreground">
          {m.workbench_browser_settings_error()}
        </p>
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded border border-border/60 px-3 py-2 text-sm hover:bg-muted/40"
          onClick={() => {
            setState("loading");
            setAttempt((value) => value + 1);
          }}
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
      {state === "loading" ? (
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

  return (
    <NativeBrowserSettings
      profileId={context.browserProfileId}
      onRequestClose={context.onRequestClose}
    />
  );
}

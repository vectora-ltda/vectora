"use client";
/* oxlint-disable react/exhaustive-effect-dependencies, react-hooks/exhaustive-deps -- native view lifecycle intentionally reads a stable container ref. */

import { useEffect, useRef, useState, type RefObject } from "react";
import { useSettingsOverlayStore } from "@/lib/stores/settings-overlay-store";

const CONFIRMATION_TIMEOUT_MS = 8_000;

export type BrowserSettingsViewStatus = "creating" | "confirmed" | "failed";

interface BrowserSettingsViewOptions {
  profileId: string | null | undefined;
  open: boolean;
  containerRef: RefObject<HTMLDivElement | null>;
  onClose?: () => void;
  retryKey?: number;
}

export function useBrowserSettingsView({
  profileId,
  open,
  containerRef,
  onClose,
  retryKey = 0,
}: BrowserSettingsViewOptions) {
  const settingsOpen = useSettingsOverlayStore((state) => state.open);
  const [status, setStatus] = useState<BrowserSettingsViewStatus>("creating");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [viewId, setViewId] = useState<number | null>(null);
  const generationRef = useRef(0);
  const closeRef = useRef(onClose);

  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const bridge =
      typeof window === "undefined" ? undefined : window.vectora?.browserView;
    const generation = ++generationRef.current;
    let disposed = false;
    let nativeViewId: number | null = null;
    let unsubscribe: (() => void) | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const destroy = () => {
      if (timer) clearTimeout(timer);
      unsubscribe?.();
      if (nativeViewId !== null) {
        bridge?.setVisible(nativeViewId, false);
        void bridge?.destroyView(nativeViewId);
      }
      nativeViewId = null;
      setViewId(null);
    };

    if (!open || settingsOpen || !bridge || !profileId) {
      // Clear the native id when the external surface closes.
      // oxlint-disable-next-line react/set-state-in-effect
      setViewId(null);
      if (open && settingsOpen) closeRef.current?.();
      if (open && !settingsOpen && (!bridge || !profileId)) {
        setStatus("failed");
        setErrorMessage("Native browser settings are unavailable.");
      }
      return destroy;
    }

    // The status mirrors the native view creation lifecycle.
    // oxlint-disable-next-line react/set-state-in-effect
    setStatus("creating");
    setErrorMessage(null);
    void bridge
      .createView({ profileId, kind: "native-settings" })
      .then(async (createdId) => {
        if (disposed || generation !== generationRef.current) {
          await bridge.destroyView(createdId);
          return;
        }
        nativeViewId = createdId;
        setViewId(createdId);
        unsubscribe = bridge.onEvent((eventViewId, event) => {
          if (eventViewId !== createdId) return;
          if (
            event.type === "navigated" &&
            event.url.startsWith("chrome://settings")
          ) {
            if (timer) clearTimeout(timer);
            setStatus("confirmed");
          } else if (event.type === "loadFailed") {
            setStatus("failed");
            setErrorMessage(
              event.errorDescription ?? "Unable to load browser settings.",
            );
            destroy();
          } else if (event.type === "escapePressed") {
            closeRef.current?.();
          }
        });
        timer = setTimeout(() => {
          setStatus("failed");
          setErrorMessage("Unable to confirm browser settings view.");
          destroy();
        }, CONFIRMATION_TIMEOUT_MS);
        const result = await bridge.navigate(createdId, "chrome://settings");
        if (!result.ok) {
          setStatus("failed");
          setErrorMessage(
            result.error ?? "Unable to navigate to browser settings.",
          );
          destroy();
        }
      })
      .catch((error: unknown) => {
        if (disposed) return;
        setStatus("failed");
        setErrorMessage(
          error instanceof Error
            ? error.message
            : "Unable to create browser settings view.",
        );
        destroy();
      });

    return () => {
      disposed = true;
      destroy();
    };
  }, [open, profileId, retryKey, settingsOpen]);

  // The container ref is intentionally read at effect time after the native view exists.
  // oxlint-disable-next-line react/exhaustive-effect-dependencies, react-hooks/exhaustive-deps
  useEffect(() => {
    const bridge =
      typeof window === "undefined" ? undefined : window.vectora?.browserView;
    const container = containerRef.current;
    if (!bridge || viewId === null || !container || !open || settingsOpen)
      return;
    const reportBounds = () => {
      const rect = container.getBoundingClientRect();
      const bounds = {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.max(0, Math.round(rect.width)),
        height: Math.max(0, Math.round(rect.height)),
      };
      bridge.setBounds(viewId, bounds);
      bridge.setVisible(
        viewId,
        status === "confirmed" && bounds.width > 0 && bounds.height > 0,
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
      bridge.setVisible(viewId, false);
    };
  }, [open, settingsOpen, status, viewId]);

  return { status, errorMessage, viewId };
}

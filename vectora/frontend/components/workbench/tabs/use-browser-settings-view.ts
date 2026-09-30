"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import { isNativeSettingsRoute } from "@/lib/browser-capabilities";

interface BrowserSettingsViewOptions {
  profileId: string;
  open: boolean;
  visible: boolean;
  settingsOverlayOpen: boolean;
  containerRef: RefObject<HTMLDivElement | null>;
  onClose: () => void;
}

interface BrowserSettingsViewState {
  viewId: number | null;
  error: boolean;
}

/** Owns the lifecycle and bounds of the native Chromium settings view. */
export function useBrowserSettingsView({
  profileId,
  open,
  visible,
  settingsOverlayOpen,
  containerRef,
  onClose,
}: BrowserSettingsViewOptions): BrowserSettingsViewState {
  const desktopBrowser =
    typeof window !== "undefined" ? window.vectora?.browserView : undefined;
  const requestRef = useRef(0);
  const viewRef = useRef<number | null>(null);
  const [viewId, setViewId] = useState<number | null>(null);
  const [error, setError] = useState(false);
  const confirmationRef = useRef(false);

  useEffect(() => {
    if (!open || settingsOverlayOpen || !desktopBrowser) return;
    const requestId = ++requestRef.current;
    confirmationRef.current = false;
    setError(false);
    void desktopBrowser
      .createView({ profileId, kind: "native-settings" })
      .then(async (createdViewId) => {
        if (requestId !== requestRef.current) {
          desktopBrowser.destroyView(createdViewId);
          return;
        }
        viewRef.current = createdViewId;
        setViewId(createdViewId);
        const result = await desktopBrowser.navigate(
          createdViewId,
          "chrome://settings",
        );
        if (!result.ok && requestId === requestRef.current) {
          setError(true);
          desktopBrowser.destroyView(createdViewId);
          viewRef.current = null;
          setViewId(null);
          return;
        }
        if (requestId !== requestRef.current) return;
        const element = containerRef.current;
        if (!element || !open || !visible || settingsOverlayOpen) return;
        const rect = element.getBoundingClientRect();
        const width = Math.max(0, Math.round(rect.width));
        const height = Math.max(0, Math.round(rect.height));
        if (width === 0 || height === 0) {
          desktopBrowser.setVisible(createdViewId, false);
          return;
        }
        desktopBrowser.setBounds(createdViewId, {
          x: Math.round(rect.left),
          y: Math.round(rect.top),
          width,
          height,
        });
        desktopBrowser.setVisible(createdViewId, true);
      })
      .catch(() => {
        if (requestId === requestRef.current) setError(true);
      });

    return () => {
      requestRef.current += 1;
    };
  }, [desktopBrowser, open, profileId, settingsOverlayOpen]);

  useEffect(() => {
    if (!desktopBrowser || viewId === null) return;
    const timeout = window.setTimeout(() => {
      if (confirmationRef.current || viewRef.current !== viewId) return;
      setError(true);
      desktopBrowser.setVisible(viewId, false);
      desktopBrowser.destroyView(viewId);
      viewRef.current = null;
      setViewId(null);
    }, 5000);
    const unsubscribe = desktopBrowser.onEvent((eventViewId, event) => {
      if (eventViewId !== viewId) return;
      if (event.type === "escapePressed") onClose();
      if (event.type === "navigated" && isNativeSettingsRoute(event.url)) {
        confirmationRef.current = true;
      }
      if (event.type === "loadFailed") {
        confirmationRef.current = false;
        setError(true);
        desktopBrowser.setVisible(viewId, false);
        desktopBrowser.destroyView(viewId);
        viewRef.current = null;
        setViewId(null);
      }
    });
    return () => {
      window.clearTimeout(timeout);
      unsubscribe();
    };
  }, [desktopBrowser, onClose, viewId]);

  useEffect(() => {
    if (!desktopBrowser || viewId === null) return;
    const shouldShow = open && visible && !settingsOverlayOpen;
    const element = containerRef.current;
    if (!shouldShow || !element) {
      desktopBrowser.setVisible(viewId, false);
      return;
    }
    const report = () => {
      const rect = element.getBoundingClientRect();
      const width = Math.max(0, Math.round(rect.width));
      const height = Math.max(0, Math.round(rect.height));
      if (width === 0 || height === 0) {
        desktopBrowser.setVisible(viewId, false);
        return;
      }
      desktopBrowser.setBounds(viewId, {
        x: Math.round(rect.left),
        y: Math.round(rect.top),
        width,
        height,
      });
      desktopBrowser.setVisible(viewId, true);
    };
    report();
    const observer = new ResizeObserver(report);
    observer.observe(element);
    window.addEventListener("resize", report);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", report);
      desktopBrowser.setVisible(viewId, false);
    };
  }, [
    containerRef,
    desktopBrowser,
    open,
    settingsOverlayOpen,
    viewId,
    visible,
  ]);

  useEffect(() => {
    if (!settingsOverlayOpen) return;
    requestRef.current += 1;
    const currentViewId = viewRef.current;
    if (desktopBrowser && currentViewId !== null) {
      desktopBrowser.setVisible(currentViewId, false);
      desktopBrowser.destroyView(currentViewId);
      viewRef.current = null;
      setViewId(null);
    }
    if (open) onClose();
  }, [desktopBrowser, onClose, open, settingsOverlayOpen]);

  useEffect(
    () => () => {
      requestRef.current += 1;
      const currentViewId = viewRef.current;
      if (desktopBrowser && currentViewId !== null) {
        desktopBrowser.setVisible(currentViewId, false);
        desktopBrowser.destroyView(currentViewId);
      }
    },
    [desktopBrowser],
  );

  return { viewId, error };
}

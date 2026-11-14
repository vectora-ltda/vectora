"use client";

import { useEffect } from "react";
import { useNavigate } from "@tanstack/react-router";
import { signalWorkspaceChoiceForNewSession } from "@/lib/stores/new-session-signal";
import { useOpenedFilesStore } from "@/lib/stores/opened-files-store";
import { useToastStore } from "@/lib/stores/toast-store";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import {
  openSystemFiles,
  sessionIdFromDeepLink,
  type OpenSystemFilesDeps,
} from "@/lib/utils/open-system-files";

/**
 * Pedidos que o sistema faz ao app desktop: arquivos e pastas ("Abrir com
 * Vectora", arrastar sobre o ícone, linha de comando) e deep-links
 * `vectora://session/<id>`. O processo principal guarda o que chegou antes de
 * esta janela montar e o entrega aqui; o que chega depois vem por push.
 */
export function useSystemOpenRequests(): void {
  const navigate = useNavigate();

  useEffect(() => {
    const bridge = typeof window === "undefined" ? undefined : window.vectora;
    if (!bridge) return;
    let active = true;

    const deps: OpenSystemFilesDeps = {
      createWorkspace: async (path) => {
        const result = await useWorkspacesStore.getState().create(path);
        return result.ok ? result.data.id : null;
      },
      addAttachments: (attachments) =>
        useOpenedFilesStore.getState().add(attachments),
      openNewSession: (workspaceId) => {
        const target =
          workspaceId ?? useWorkspacesStore.getState().active_id ?? null;
        if (target) signalWorkspaceChoiceForNewSession(target);
        void navigate({
          to: "/session/$threadId",
          params: { threadId: "new" },
        });
      },
      notifyError: (message) => useToastStore.getState().error(message),
    };
    const handleFiles = (files: Parameters<typeof openSystemFiles>[0]) => {
      if (active && files.length > 0) void openSystemFiles(files, deps);
    };
    const handleDeepLink = (url: string) => {
      bridge.acknowledgeDeepLink?.(url);
      const threadId = sessionIdFromDeepLink(url);
      if (active && threadId) {
        void navigate({ to: "/session/$threadId", params: { threadId } });
      }
    };

    void bridge.takeOpenFiles?.().then(handleFiles);
    void bridge.takeDeepLink?.().then((url) => {
      if (url) handleDeepLink(url);
    });
    const stopFiles = bridge.onOpenFiles?.(handleFiles);
    const stopLinks = bridge.onDeepLink?.(handleDeepLink);
    return () => {
      active = false;
      stopFiles?.();
      stopLinks?.();
    };
  }, [navigate]);
}

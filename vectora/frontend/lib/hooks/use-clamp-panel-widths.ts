import { useEffect, useRef } from "react";

import { useSettingsStore } from "@/lib/stores/settings-store";
import { useWorkbenchStore } from "@/lib/stores/workbench-store";
import { getUnscaledViewportWidth } from "@/lib/hooks/use-media-query";
import { getResponsivePanelWidths } from "@/lib/layout/responsive-panel-width";

/**
 * Um painel resizável persiste a própria largura em px. Se o usuário abriu
 * uma janela larga, redimensionou o painel, e depois volta numa tela
 * estreita (ou encolhe a janela do Electron), a largura antiga persistida
 * pode ultrapassar a viewport inteira — o painel sozinho já causaria
 * overflow horizontal da página. Clampa contra a viewport atual no mount e
 * a cada resize.
 */
export function useClampPanelWidths(): void {
  const sidebarWidth = useSettingsStore((s) => s.sidebarWidth);
  const setSidebarWidth = useSettingsStore((s) => s.setSidebarWidth);
  const chatSidebarWidth = useSettingsStore((s) => s.chatSidebarWidth);
  const setChatSidebarWidth = useSettingsStore((s) => s.setChatSidebarWidth);
  const splitSize = useWorkbenchStore((s) => s.splitSize);
  const setSplitSize = useWorkbenchStore((s) => s.setSplitSize);
  const preferredWidths = useRef({ sidebarWidth, chatSidebarWidth, splitSize });

  // Keep the user's preferred desktop widths while a narrow viewport applies
  // temporary clamps. This lets the panels recover when the window widens.
  useEffect(() => {
    if (getUnscaledViewportWidth() >= 1024) {
      preferredWidths.current = { sidebarWidth, chatSidebarWidth, splitSize };
    }
  }, [sidebarWidth, chatSidebarWidth, splitSize]);

  useEffect(() => {
    const clamp = () => {
      const viewportWidth = getUnscaledViewportWidth();
      const next = getResponsivePanelWidths(preferredWidths.current, viewportWidth);

      if (sidebarWidth !== next.sidebarWidth) {
        setSidebarWidth(next.sidebarWidth);
      }
      if (chatSidebarWidth !== next.chatSidebarWidth) {
        setChatSidebarWidth(next.chatSidebarWidth);
      }
      if (splitSize !== next.splitSize) setSplitSize(next.splitSize);
    };

    clamp();
    window.addEventListener("resize", clamp);
    return () => window.removeEventListener("resize", clamp);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    chatSidebarWidth,
    setChatSidebarWidth,
    setSidebarWidth,
    setSplitSize,
    sidebarWidth,
    splitSize,
  ]);
}

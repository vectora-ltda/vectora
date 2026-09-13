import { useEffect, useState } from "react";

import { getUnscaledViewportWidth } from "@/lib/hooks/use-media-query";
import {
  getResponsivePanelWidths,
  type PanelWidths,
} from "@/lib/layout/responsive-panel-width";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { useWorkbenchStore } from "@/lib/stores/workbench-store";

/**
 * Retorna larguras visíveis responsivas sem gravar clamps temporários nos
 * stores persistidos. A preferência original do usuário é reaplicada quando
 * a viewport volta a uma faixa ampla.
 */
export function useClampPanelWidths(): PanelWidths {
  const sidebarWidth = useSettingsStore((s) => s.sidebarWidth);
  const chatSidebarWidth = useSettingsStore((s) => s.chatSidebarWidth);
  const splitSize = useWorkbenchStore((s) => s.splitSize);
  const [viewportWidth, setViewportWidth] = useState(getUnscaledViewportWidth);

  useEffect(() => {
    const update = () => setViewportWidth(getUnscaledViewportWidth());
    window.addEventListener("resize", update);
    window.visualViewport?.addEventListener("resize", update);
    return () => {
      window.removeEventListener("resize", update);
      window.visualViewport?.removeEventListener("resize", update);
    };
  }, []);

  return getResponsivePanelWidths(
    { sidebarWidth, chatSidebarWidth, splitSize },
    viewportWidth,
  );
}

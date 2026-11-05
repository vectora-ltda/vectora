"use client";

/**
 * WindowDock — barra inferior com as janelas minimizadas. Clicar restaura a
 * janela (e a traz pro topo). Fica oculto quando não há nada minimizado.
 */

import { FileText } from "lucide-react";
import { useWindowsStore } from "@/lib/stores/windows-store";
import { m } from "@/lib/paraglide/messages";
export function WindowDock() {
  const windows = useWindowsStore((s) => s.windows);
  const restore = useWindowsStore((s) => s.restore);
  const minimized = windows.filter((w) => w.minimized);
  if (minimized.length === 0) return null;
  return (
    <div className="safe-area-bottom-dock fixed bottom-2 left-1/2 -translate-x-1/2 z-[70] flex items-center gap-1 px-1.5 py-1 rounded-lg border border-border bg-card/95 shadow-xl backdrop-blur">
      {minimized.map((win) => (
        <button
          key={win.id}
          onClick={() => restore(win.id)}
          className="flex items-center gap-1.5 max-w-[180px] px-2 py-1 rounded text-xs hover:bg-muted/60"
          title={m.window_restore()}
        >
          <FileText className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate">{win.title}</span>
        </button>
      ))}
    </div>
  );
}

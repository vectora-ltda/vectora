"use client";

/**
 * SidebarFolders
 *
 * Painel "Pastas" no topo da sidebar com acesso rápido a:
 * - Workspaces ativos do usuário (click → ativa).
 * - Safe-roots configurados (click → abre trust dialog pré-navegado).
 *
 * Colapsável; mantém o estado expandido em localStorage por usuário.
 */

import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, FolderLock, Folder } from "lucide-react";

import {
  useWorkspacesStore,
  type SafeRootSummary,
  type WorkspaceInfo,
} from "@/lib/stores/workspaces-store";
import { WorkspaceTrustDialog } from "./workspace-trust-dialog";
import { m } from "@/lib/paraglide/messages";

const STATE_KEY = "vectora:sidebar:folders-open";

function shortName(path: string): string {
  // Último segmento (Windows ou POSIX); fallback no path inteiro.
  const match = path.match(/[/\\]([^/\\]+)[/\\]?$/);
  return match?.[1] ?? path;
}

export function SidebarFolders() {
  const workspaces = useWorkspacesStore((s) => s.workspaces) ?? [];
  const activeId = useWorkspacesStore((s) => s.active_id);
  const safeRoots = useWorkspacesStore((s) => s.safeRoots) ?? [];
  const setActive = useWorkspacesStore((s) => s.setActive);
  const loadSafeRoots = useWorkspacesStore((s) => s.loadSafeRoots);

  const [open, setOpen] = useState(true);
  const [trustOpen, setTrustOpen] = useState(false);
  const [trustInitialPath, setTrustInitialPath] = useState<string | undefined>(
    undefined,
  );

  // Hidrata estado persistido + safe-roots na montagem.
  useEffect(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem(STATE_KEY);
      // localStorage não existe no SSR — leitura de sistema externo, só
      // pode rodar no cliente.
      // oxlint-disable-next-line react/set-state-in-effect
      if (saved === "0") setOpen(false);
    }
    void loadSafeRoots();
  }, [loadSafeRoots]);

  const toggle = () => {
    setOpen((v) => {
      const next = !v;
      if (typeof window !== "undefined") {
        localStorage.setItem(STATE_KEY, next ? "1" : "0");
      }
      return next;
    });
  };

  const openRoot = (root: SafeRootSummary) => {
    setTrustInitialPath(root.path);
    setTrustOpen(true);
  };

  const isEmpty = workspaces.length === 0 && safeRoots.length === 0;

  return (
    <>
      <div className="px-3 pt-2 pb-1">
        <button
          onClick={toggle}
          className="w-full flex items-center gap-1.5 px-1 py-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground transition-colors"
          aria-expanded={open}
        >
          {open ? (
            <ChevronDown className="w-3 h-3" />
          ) : (
            <ChevronRight className="w-3 h-3" />
          )}
          {m.sidebar_folders()}
        </button>

        {open && (
          <div className="mt-1 space-y-0.5">
            {workspaces.map((ws: WorkspaceInfo) => {
              const active = ws.id === activeId;
              return (
                <button
                  key={`ws-${ws.id}`}
                  onClick={() => void setActive(ws.id)}
                  title={ws.cwd}
                  className={`w-full flex items-center gap-2 px-2 py-1.5 text-sm rounded-md transition-colors text-left ${
                    active
                      ? "bg-muted/60 text-foreground border border-border/60"
                      : "text-sidebar-foreground hover:bg-muted/50"
                  }`}
                >
                  <Folder className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
                  <span className="truncate flex-1 text-xs">
                    {ws.name || shortName(ws.cwd)}
                  </span>
                  {active && (
                    <span className="w-1.5 h-1.5 rounded-full bg-foreground/40 shrink-0" />
                  )}
                </button>
              );
            })}

            {safeRoots.map((root) => (
              <button
                key={`sr-${root.id}`}
                onClick={() => openRoot(root)}
                title={root.path}
                className="w-full flex items-center gap-2 px-2 py-1.5 text-sm rounded-md text-sidebar-foreground hover:bg-muted/50 transition-colors text-left"
              >
                <FolderLock className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
                <span className="truncate flex-1 text-xs">{root.label}</span>
              </button>
            ))}

            {isEmpty && (
              <p className="px-2 py-2 text-[11px] text-muted-foreground italic">
                {m.sidebar_folders_empty()}
              </p>
            )}
          </div>
        )}
      </div>

      <WorkspaceTrustDialog
        open={trustOpen}
        onOpenChange={setTrustOpen}
        initialPath={trustInitialPath}
      />
    </>
  );
}

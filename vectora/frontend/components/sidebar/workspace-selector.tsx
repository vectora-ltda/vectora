"use client";

/**
 * WorkspaceSelector
 *
 * Chip de pasta — vive no rodapé do composer, integrado aos demais controles
 * compactos da entrada de mensagem.
 * Mostra o workspace ativo e, ao clicar, abre um dropdown com a lista de
 * workspaces conhecidos (com indicador de confiança) e a opção de adicionar
 * uma nova pasta via trust dialog.
 *
 * `compact` ajusta o gatilho para a escala dos demais chips do rodapé
 * (`PermissionModeMenu`, `SelectTrigger` de modelo: `text-xs h-7 px-2`) —
 * o dropdown e a lógica de seleção/confiança permanecem inalterados.
 */

import { useEffect, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  Cloud,
  Download,
  FileText,
  FolderGit2,
  FolderOpen,
  Image as ImageIcon,
  Monitor,
  Music,
  Plus,
  Server,
  Video,
  type LucideIcon,
} from "lucide-react";

import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { useNetworkStatus } from "@/lib/hooks/use-network-status";
import { ErrorBanner } from "@/components/ui/error-banner";
import { WorkspaceTrustDialog } from "./workspace-trust-dialog";
import { m } from "@/lib/paraglide/messages";

interface WorkspaceSelectorProps {
  compact?: boolean;
}

/**
 * Pastas especiais do sistema (Windows/macOS/Linux, en/pt-BR) com ícones e
 * cores que lembram o gerenciador de arquivos do SO — facilita reconhecer
 * "Área de Trabalho", "Downloads", "Imagens" etc. de relance.
 */
const SPECIAL_FOLDER_ICONS: Record<
  string,
  { icon: LucideIcon; className: string }
> = {
  desktop: { icon: Monitor, className: "text-sky-500" },
  "área de trabalho": { icon: Monitor, className: "text-sky-500" },
  "area de trabalho": { icon: Monitor, className: "text-sky-500" },
  documents: { icon: FileText, className: "text-blue-500" },
  documentos: { icon: FileText, className: "text-blue-500" },
  downloads: { icon: Download, className: "text-emerald-500" },
  pictures: { icon: ImageIcon, className: "text-pink-500" },
  imagens: { icon: ImageIcon, className: "text-pink-500" },
  fotos: { icon: ImageIcon, className: "text-pink-500" },
  videos: { icon: Video, className: "text-red-500" },
  vídeos: { icon: Video, className: "text-red-500" },
  music: { icon: Music, className: "text-orange-500" },
  música: { icon: Music, className: "text-orange-500" },
  músicas: { icon: Music, className: "text-orange-500" },
  musicas: { icon: Music, className: "text-orange-500" },
};

function getSpecialFolderIcon(name: string) {
  return SPECIAL_FOLDER_ICONS[name.trim().toLowerCase()];
}

/** Ícone do workspace: pasta especial do SO > repositório git > pasta genérica. */
function WorkspaceFolderIcon({
  workspace,
  className,
}: {
  workspace: { name: string; is_git_repo: boolean };
  className: string;
}) {
  const special = getSpecialFolderIcon(workspace.name);
  if (special) {
    const Icon = special.icon;
    return <Icon className={`${className} ${special.className}`} />;
  }
  if (workspace.is_git_repo) {
    return <FolderGit2 className={`${className} text-primary`} />;
  }
  return <FolderOpen className={`${className} text-muted-foreground`} />;
}

export function WorkspaceSelector({ compact = false }: WorkspaceSelectorProps) {
  // Criar/confiar/inicializar workspace exigem o backend; offline
  // essas ações só produziriam erro silencioso.
  const { offline } = useNetworkStatus();
  const workspaces = useWorkspacesStore((s) => s.workspaces);
  const hydrate = useWorkspacesStore((s) => s.hydrate);
  const safeRoots = useWorkspacesStore((s) => s.safeRoots) ?? [];
  const activeId = useWorkspacesStore((s) => s.active_id);
  const status = useWorkspacesStore((s) => s.status);
  const error = useWorkspacesStore((s) => s.error);
  const setActive = useWorkspacesStore((s) => s.setActive);

  const [open, setOpen] = useState(false);
  const [trustOpen, setTrustOpen] = useState(false);
  const [trustInitialPath, setTrustInitialPath] = useState<
    string | undefined
  >();
  const ref = useRef<HTMLDivElement>(null);

  // Fecha ao clicar fora
  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node))
        setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const active =
    workspaces.find((w) => w.id === activeId) ?? workspaces[0] ?? null;

  return (
    <>
      <div className="relative" ref={ref}>
        <button
          onClick={() => setOpen((o) => !o)}
          className={
            compact
              ? "flex items-center gap-1.5 h-7 px-2 rounded-md text-xs text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors select-none min-w-0 max-w-[160px]"
              : "flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-sm text-foreground/80 hover:text-foreground hover:bg-muted/50 transition-colors select-none min-w-0 max-w-[200px]"
          }
          title={active?.cwd ?? m.workspace_select_title()}
          aria-expanded={open}
        >
          {active ? (
            <WorkspaceFolderIcon
              workspace={active}
              className={`shrink-0 ${compact ? "w-3.5 h-3.5" : "w-4 h-4"}`}
            />
          ) : (
            <FolderOpen
              className={`shrink-0 text-muted-foreground ${compact ? "w-3.5 h-3.5" : "w-4 h-4"}`}
            />
          )}
          <span className="truncate font-medium">
            {active?.name ?? m.workspace_add_folder()}
          </span>
          {active?.transport === "ssh" && (
            <span
              className="shrink-0"
              title={`${m.workspace_transport_ssh()}: ${active.remote_host ?? ""}`}
            >
              <Server className="w-3.5 h-3.5 text-sky-500" />
            </span>
          )}
          {active?.transport === "codespace" && (
            <span
              className="shrink-0"
              title={`${m.workspace_transport_codespace()}: ${active.codespace_name ?? ""}`}
            >
              <Cloud className="w-3.5 h-3.5 text-violet-500" />
            </span>
          )}
          <ChevronDown className="w-3.5 h-3.5 shrink-0 text-muted-foreground [&_svg]:opacity-70" />
        </button>

        {open && (
          <div
            className={`absolute left-0 z-50 w-72 rounded-lg border border-border bg-background shadow-xl py-1 animate-in fade-in slide-in-from-top-2 ${
              compact ? "bottom-9" : "top-10"
            }`}
          >
            <div className="px-3 py-2 text-xs font-medium text-muted-foreground uppercase tracking-wide">
              {m.workspace_select_title()}
            </div>

            {status === "error" && error && (
              <div className="px-2 pb-2">
                <ErrorBanner message={error} onRetry={() => hydrate()} />
              </div>
            )}

            <div className="max-h-72 overflow-y-auto">
              {workspaces.length === 0 && (
                <p className="px-3 py-2 text-sm text-muted-foreground">
                  {m.workspace_no_workspaces()}
                </p>
              )}

              {workspaces.map((w) => (
                <button
                  key={w.id}
                  className="w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-accent text-left transition-colors"
                  onClick={() => {
                    void setActive(w.id);
                    setOpen(false);
                  }}
                >
                  {w.id === active?.id ? (
                    <Check className="w-4 h-4 shrink-0 text-primary" />
                  ) : w.transport === "ssh" ? (
                    <Server className="w-4 h-4 shrink-0 text-sky-500" />
                  ) : w.transport === "codespace" ? (
                    <Cloud className="w-4 h-4 shrink-0 text-violet-500" />
                  ) : (
                    <WorkspaceFolderIcon
                      workspace={w}
                      className="w-4 h-4 shrink-0"
                    />
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium text-foreground">
                      {w.name}
                    </span>
                    <span className="block truncate text-xs text-muted-foreground font-mono">
                      {w.cwd}
                    </span>
                  </span>
                </button>
              ))}

              {safeRoots.map((root) => (
                <button
                  key={`safe-root-${root.id}`}
                  className="w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-accent text-left transition-colors"
                  title={root.path}
                  onClick={() => {
                    setTrustInitialPath(root.path);
                    setOpen(false);
                    setTrustOpen(true);
                  }}
                >
                  <FolderOpen className="w-4 h-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium text-foreground">
                      {root.label}
                    </span>
                    <span className="block truncate text-xs text-muted-foreground font-mono">
                      {root.path}
                    </span>
                  </span>
                </button>
              ))}
            </div>

            <div className="border-t border-border/60 mt-1 pt-1">
              <button
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-foreground/80 hover:text-foreground hover:bg-accent transition-colors text-left disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:text-foreground/80"
                disabled={offline}
                title={offline ? m.network_disabled_offline() : undefined}
                onClick={() => {
                  setOpen(false);
                  setTrustOpen(true);
                }}
              >
                <Plus className="w-4 h-4 shrink-0 text-muted-foreground" />
                {m.workspace_add_folder()}
              </button>
            </div>
          </div>
        )}
      </div>

      <WorkspaceTrustDialog
        open={trustOpen}
        onOpenChange={(nextOpen) => {
          setTrustOpen(nextOpen);
          if (!nextOpen) setTrustInitialPath(undefined);
        }}
        initialPath={trustInitialPath}
      />
    </>
  );
}

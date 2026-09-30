"use client";

/**
 * TerminalPanel — container de terminais: tabs por instância, botão de abrir
 * novo e fechar. Cada aba renderiza um XtermView ligado ao seu próprio PTY no
 * backend (identificado por terminal_id).
 */

import {
  Loader2,
  Plug,
  Plus,
  RefreshCcw,
  ShieldCheck,
  TerminalSquare,
  X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  useTerminalsStore,
  type TerminalInstance,
} from "@/lib/stores/terminals-store";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { XtermView } from "./xterm-view";
import { m } from "@/lib/paraglide/messages";
import { WorkbenchDialog } from "@/components/workbench/workbench-dialog";
import type { WorkbenchSettingsContext } from "@/lib/types/workbench-settings";
import { Button } from "@/components/ui/button";
import { apiFsCreateFile } from "@/components/workbench/files/files-api";
import { apiUpdateFile, fetchFile } from "@/lib/api/fs-files";

interface SandboxStatus {
  enabled: boolean;
  diagnostic: string | null;
}

const DEFAULT_SANDBOX_TOML = `[sandbox]
enabled = true
backend = "local"
`;

const SANDBOX_DIAGNOSTIC_MESSAGE: Record<string, () => string> = {
  no_workspace: m.terminal_sandbox_diagnostic_no_workspace,
  no_vectora_toml: m.terminal_sandbox_diagnostic_no_vectora_toml,
  sandbox_disabled_in_config:
    m.terminal_sandbox_diagnostic_sandbox_disabled_in_config,
  wsl_not_installed: m.terminal_sandbox_diagnostic_wsl_not_installed,
  no_general_purpose_distro:
    m.terminal_sandbox_diagnostic_no_general_purpose_distro,
  distro_missing_shell: m.terminal_sandbox_diagnostic_distro_missing_shell,
};

/** Consulta `GET /workspaces/{id}/sandbox/status` — reflete se o worker
 * jailado (Sandbox) está ativo pra essa workspace, e por quê não quando
 * desabilitado. `null` enquanto carrega (não mostra nenhum dos dois
 * avisos até saber de verdade). */
function useSandboxStatus(workspaceId: string | undefined): {
  status: SandboxStatus | null;
  refetch: () => void;
} {
  const [status, setStatus] = useState<SandboxStatus | null>(null);

  const refetch = useCallback(() => {
    if (!workspaceId) {
      setStatus(null);
      return;
    }
    fetch(`/workspaces/${encodeURIComponent(workspaceId)}/sandbox/status`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data: SandboxStatus | null) => {
        setStatus(
          data
            ? { enabled: data.enabled, diagnostic: data.diagnostic ?? null }
            : { enabled: false, diagnostic: null },
        );
      })
      .catch(() => {
        setStatus({ enabled: false, diagnostic: null });
      });
  }, [workspaceId]);

  useEffect(() => {
    // Sincroniza com o fetch de status da sandbox (sistema externo) — zera
    // antes de revalidar pra não mostrar o status da workspace anterior.
    // oxlint-disable-next-line react/set-state-in-effect
    setStatus(null);
    refetch();
  }, [refetch]);

  return { status, refetch };
}

function SandboxSettingsForm({
  workspaceId,
  diagnostic,
  open,
  onOpenChange,
  onInitDone,
}: {
  workspaceId: string;
  diagnostic: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onInitDone: () => void;
}) {
  const [initializing, setInitializing] = useState(false);
  const [loadingFile, setLoadingFile] = useState(false);
  const [savingFile, setSavingFile] = useState(false);
  const [fileExists, setFileExists] = useState(false);
  const [content, setContent] = useState(DEFAULT_SANDBOX_TOML);
  const [fileSha256, setFileSha256] = useState<string | null>(null);
  const [initError, setInitError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveHint, setSaveHint] = useState<string | null>(null);

  const message = diagnostic
    ? (SANDBOX_DIAGNOSTIC_MESSAGE[diagnostic]?.() ?? diagnostic)
    : "";

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    // Sincroniza com o fetch do vectora.toml (sistema externo) ao abrir o
    // dialog — não deriva de estado puro.
    // oxlint-disable-next-line react/set-state-in-effect
    setLoadingFile(true);
    setSaveError(null);
    setSaveHint(null);
    fetchFile(workspaceId, "vectora.toml")
      .then((file) => {
        if (cancelled) return;
        if (typeof file?.content === "string") {
          setFileExists(true);
          setContent(file.content);
          setFileSha256(file.sha256 ?? null);
        } else {
          setFileExists(false);
          setContent(DEFAULT_SANDBOX_TOML);
          setFileSha256(null);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFileExists(false);
          setContent(DEFAULT_SANDBOX_TOML);
          setFileSha256(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingFile(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, workspaceId]);

  async function handleInit() {
    setInitializing(true);
    setInitError(null);
    try {
      const res = await fetch(
        `/workspaces/${encodeURIComponent(workspaceId)}/sandbox/init`,
        { method: "POST" },
      );
      if (!res.ok) {
        setInitError(m.terminal_sandbox_init_error());
        return;
      }
      onOpenChange(false);
      onInitDone();
    } catch {
      setInitError(m.terminal_sandbox_init_error());
    } finally {
      setInitializing(false);
    }
  }

  async function handleSave() {
    setSavingFile(true);
    setSaveError(null);
    setSaveHint(null);
    try {
      if (fileExists) {
        const result = await apiUpdateFile(
          workspaceId,
          "vectora.toml",
          content,
          fileSha256,
        );
        if (!result.ok) {
          setSaveError(
            result.conflict
              ? m.terminal_sandbox_editor_conflict()
              : (result.message ?? m.terminal_sandbox_init_error()),
          );
          return;
        }
        setFileSha256(result.sha256);
      } else {
        const created = await apiFsCreateFile(
          workspaceId,
          "vectora.toml",
          content,
        );
        if (!created.ok) {
          setSaveError(created.message ?? m.terminal_sandbox_init_error());
          return;
        }
        const refreshed = await fetchFile(workspaceId, "vectora.toml");
        setFileExists(true);
        setFileSha256(refreshed?.sha256 ?? null);
      }
      setSaveHint(m.terminal_sandbox_editor_saved());
      onInitDone();
    } catch {
      setSaveError(m.terminal_sandbox_init_error());
    } finally {
      setSavingFile(false);
    }
  }

  return (
    <WorkbenchDialog
      open={open}
      onOpenChange={onOpenChange}
      title={m.terminal_sandbox_dialog_title()}
      description={m.terminal_sandbox_editor_autosync_hint()}
      testId="terminal-settings-panel"
    >
      <div className="space-y-4 p-4">
        <p className="text-sm text-muted-foreground">{message}</p>
        {initError && <p className="text-xs text-destructive">{initError}</p>}
        <div className="flex items-start justify-between gap-3 rounded-md border border-border/60 bg-muted/20 px-3 py-2 text-xs">
          <div className="space-y-1">
            <p className="font-medium text-foreground">
              {m.terminal_sandbox_editor_title()}
            </p>
            <p className="text-muted-foreground">
              {fileExists
                ? m.terminal_sandbox_editor_existing_hint()
                : m.terminal_sandbox_editor_new_hint()}
            </p>
          </div>
          <div className="flex shrink-0 gap-2">
            {diagnostic === "no_vectora_toml" && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => void handleInit()}
                disabled={initializing || loadingFile}
              >
                {initializing ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  m.terminal_sandbox_init_button()
                )}
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setContent(DEFAULT_SANDBOX_TOML);
                setSaveError(null);
                setSaveHint(null);
              }}
              disabled={loadingFile}
            >
              <RefreshCcw className="h-3.5 w-3.5" />
              {m.terminal_sandbox_editor_reset()}
            </Button>
          </div>
        </div>
        <div className="space-y-2">
          {loadingFile ? (
            <div className="flex min-h-[18rem] items-center justify-center rounded-md border border-border/60 bg-muted/20">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <textarea
              className="min-h-[18rem] w-full resize-y rounded-md border border-border/60 bg-background p-3 font-mono text-xs outline-none focus:border-primary"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              spellCheck={false}
            />
          )}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-h-4 text-xs">
              {saveError ? (
                <p className="text-destructive">{saveError}</p>
              ) : saveHint ? (
                <p className="text-emerald-600 dark:text-emerald-400">
                  {saveHint}
                </p>
              ) : (
                <p className="text-muted-foreground">
                  {m.terminal_sandbox_editor_autosync_hint()}
                </p>
              )}
            </div>
            <Button
              size="sm"
              onClick={() => void handleSave()}
              disabled={loadingFile || savingFile}
            >
              {savingFile ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : null}
              {fileExists
                ? m.terminal_sandbox_editor_save()
                : m.terminal_sandbox_editor_create()}
            </Button>
          </div>
        </div>
      </div>
    </WorkbenchDialog>
  );
}

/** Entrada do contrato global de configurações para o sandbox do Terminal. */
export function TerminalSettings({ workspaceId }: WorkbenchSettingsContext) {
  const [open, setOpen] = useState(false);
  const { status, refetch } = useSandboxStatus(workspaceId ?? undefined);

  if (!workspaceId) {
    return (
      <p className="text-sm text-muted-foreground">
        {m.workbench_settings_missing_workspace()}
      </p>
    );
  }

  return (
    <div className="min-w-0 space-y-3">
      <p className="text-sm text-muted-foreground">
        {status?.enabled
          ? m.terminal_sandbox_active()
          : m.terminal_no_sandbox_warning()}
      </p>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        {m.terminal_sandbox_configure_link()}
      </Button>
      <SandboxSettingsForm
        workspaceId={workspaceId}
        diagnostic={status?.diagnostic ?? null}
        open={open}
        onOpenChange={setOpen}
        onInitDone={refetch}
      />
    </div>
  );
}

interface TerminalPanelProps {
  threadId: string;
}

function newId(): string {
  return Math.random().toString(36).slice(2, 14);
}

export function TerminalPanel({ threadId }: TerminalPanelProps) {
  const terminals = useTerminalsStore((s) => s.list(threadId));
  const active = useTerminalsStore((s) => s.active(threadId));
  const open = useTerminalsStore((s) => s.open);
  const close = useTerminalsStore((s) => s.close);
  const setActive = useTerminalsStore((s) => s.setActive);
  const workspace = useWorkspacesStore((s) => s.getActive());
  const { status: sandboxStatus, refetch: refetchSandboxStatus } =
    useSandboxStatus(workspace?.id);
  const [sandboxDialogOpen, setSandboxDialogOpen] = useState(false);

  // Abre 1 terminal automaticamente quando o painel monta sem nenhum.
  // Lê o store inline (não a captura reativa) — Strict Mode roda effects
  // 2× em dev e a captura levaria a abrir 2 shells.
  useEffect(() => {
    const current = useTerminalsStore.getState().list(threadId);
    if (current.length === 0 && workspace?.trusted) {
      open(threadId, {
        id: newId(),
        title: m.terminal_tab_default(),
        workspaceId: workspace.id,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!workspace) {
    return (
      <div className="h-full flex items-center justify-center text-xs text-muted-foreground">
        {m.terminal_no_workspace()}
      </div>
    );
  }

  if (!workspace.trusted) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-2 text-xs text-muted-foreground p-4 text-center">
        <Plug className="w-6 h-6" />
        <p className="font-medium">{m.terminal_untrusted_title()}</p>
        <p className="opacity-70">{m.terminal_untrusted_hint()}</p>
      </div>
    );
  }

  const handleNew = () => {
    open(threadId, {
      id: newId(),
      title: m.terminal_tab_default(),
      workspaceId: workspace.id,
    });
  };

  return (
    <div className="h-full flex flex-col bg-sidebar">
      {/* Tabs + ações */}
      <div className="flex items-center gap-1 bg-sidebar border-b border-border/60 px-2 py-1 overflow-x-auto">
        {terminals.map((term) => (
          <button
            key={term.id}
            onClick={() => setActive(threadId, term.id)}
            className={`group flex items-center gap-1.5 pl-2 pr-1 py-1 rounded-md text-xs select-none transition-colors shrink-0 ${
              term.id === active?.id
                ? "bg-muted text-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
            }`}
          >
            <TerminalSquare className="w-3.5 h-3.5" />
            <span className="truncate max-w-[120px]">{term.title}</span>
            <span
              role="button"
              tabIndex={0}
              className="opacity-0 group-hover:opacity-100 hover:bg-muted-foreground/20 rounded-sm p-0.5"
              onClick={(e) => {
                e.stopPropagation();
                close(threadId, term.id);
              }}
            >
              <X className="w-3 h-3" />
            </span>
          </button>
        ))}
        <button
          onClick={handleNew}
          className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/50 shrink-0"
          title={m.terminal_new()}
        >
          <Plus className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Indicador dinâmico: sandboxed (informativo) vs sem sandbox (aviso,
          acionável via dialog de diagnóstico). sandboxStatus === null
          enquanto o status ainda carrega — não mostra nenhum dos dois até
          saber de verdade. */}
      {sandboxStatus?.enabled === true && (
        <div className="flex items-center gap-1.5 px-3 py-1.5 text-[10px] text-emerald-600 bg-emerald-500/10 border-b border-emerald-500/20">
          <ShieldCheck className="w-3 h-3 shrink-0" />
          {m.terminal_sandbox_active()}
        </div>
      )}
      {sandboxStatus?.enabled === false && (
        <div className="flex items-center gap-2 px-3 py-1.5 text-[10px] text-amber-600 bg-amber-500/10 border-b border-amber-500/20">
          <span>{m.terminal_no_sandbox_warning()}</span>
          <button
            type="button"
            className="underline underline-offset-2 hover:text-amber-500 shrink-0"
            onClick={() => setSandboxDialogOpen(true)}
          >
            {m.terminal_sandbox_configure_link()}
          </button>
        </div>
      )}
      {workspace && (
        <SandboxSettingsForm
          workspaceId={workspace.id}
          diagnostic={sandboxStatus?.diagnostic ?? null}
          open={sandboxDialogOpen}
          onOpenChange={setSandboxDialogOpen}
          onInitDone={refetchSandboxStatus}
        />
      )}

      {/* Body — só renderiza o terminal ativo (poupa CPU; estado fica no PTY) */}
      <div className="flex-1 relative">
        {terminals.map((term) => (
          <div
            key={term.id}
            className="absolute inset-0"
            style={{
              visibility: term.id === active?.id ? "visible" : "hidden",
            }}
          >
            <XtermView
              terminalId={term.id}
              threadId={threadId}
              workspaceId={term.workspaceId}
              onClosed={() => close(threadId, term.id)}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

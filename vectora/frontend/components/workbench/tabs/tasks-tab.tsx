"use client";

/**
 * Workbench "Tarefas" — rotinas/webhooks em segundo plano da sessão atual.
 *
 * Lista as tarefas em segundo plano da sessão (toggle, rodar agora, remover),
 * um formulário de criação e o log de execuções com link para a thread-resultado.
 * Atualiza ao vivo via SSE de webhooks (eventos `background_run.*`).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  ChevronRight,
  Loader2,
  Plus,
  Play,
  Trash2,
  ExternalLink,
  ListTodo,
} from "lucide-react";

import {
  useBackgroundTasks,
  type BackgroundKind,
  type CreateTaskInput,
  type TriggerType,
} from "@/lib/hooks/use-background-tasks";
import { useWebhookEvents } from "@/lib/hooks/use-webhook-events";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { Switch } from "@/components/ui/switch";
import { WorkbenchDialog } from "@/components/workbench/workbench-dialog";
import { m } from "@/lib/paraglide/messages";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { useToastStore } from "@/lib/stores/toast-store";

interface DraftState {
  kind: BackgroundKind;
  name: string;
  instruction: string;
  trigger_type: TriggerType;
  cron_expr: string;
  provider: string;
  events: string;
}

const EMPTY_DRAFT: DraftState = {
  kind: "routine",
  name: "",
  instruction: "",
  trigger_type: "interval",
  cron_expr: "0 9 * * *",
  provider: "github",
  events: "push",
};

function statusLabel(status: string): string {
  if (status === "done") return m.background_status_done();
  if (status === "error") return m.background_status_error();
  if (status === "awaiting_approval") return m.background_status_awaiting();
  if (status === "cancelled") return m.background_status_cancelled();
  return m.background_status_running();
}

interface BackgroundRun {
  id: string;
  task_id: string;
  run_thread_id: string | null;
  trigger_source: string;
  status: string;
  summary: string | null;
  started_at: string;
  finished_at: string | null;
}

/** Card de execução — colapsado por padrão (título curto, quebra em
 * múltiplas linhas nunca overflow horizontal); clicar expande revelando o
 * `summary` completo. Botões de ação (aprovar/rejeitar/cancelar/abrir
 * thread) ficam sempre visíveis no cabeçalho, independente do estado de
 * expansão — nunca escondidos atrás de um clique extra. */
function RunItem({
  run,
  onResolve,
  onOpenThread,
}: {
  run: BackgroundRun;
  onResolve: (decision: "approve" | "reject" | "cancel") => void;
  onOpenThread: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = Boolean(run.summary?.trim());

  return (
    <li className="border border-border/40 rounded overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-2 py-1">
        <button
          type="button"
          onClick={() => hasDetail && setExpanded((v) => !v)}
          aria-expanded={expanded}
          disabled={!hasDetail}
          className="flex min-w-0 flex-1 items-center gap-1 text-left disabled:cursor-default"
        >
          {hasDetail && (
            <ChevronRight
              className={`w-3 h-3 shrink-0 text-muted-foreground transition-transform ${
                expanded ? "rotate-90" : ""
              }`}
            />
          )}
          <span
            className={`shrink-0 ${
              run.status === "error"
                ? "text-destructive"
                : run.status === "done"
                  ? "text-emerald-500"
                  : "text-amber-500"
            }`}
          >
            {statusLabel(run.status)}
          </span>
          <span className="min-w-0 flex-1 break-words text-muted-foreground line-clamp-1">
            {run.summary ?? ""}
          </span>
        </button>
        <div className="flex items-center gap-1 shrink-0">
          {run.status === "awaiting_approval" && (
            <>
              <button
                onClick={() => onResolve("approve")}
                className="px-1.5 py-0.5 text-[10px] rounded bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/25"
              >
                {m.background_approve()}
              </button>
              <button
                onClick={() => onResolve("reject")}
                className="px-1.5 py-0.5 text-[10px] rounded bg-muted hover:bg-muted/70"
              >
                {m.background_reject()}
              </button>
              <button
                onClick={() => onResolve("cancel")}
                className="px-1.5 py-0.5 text-[10px] rounded bg-destructive/15 text-destructive hover:bg-destructive/25"
              >
                {m.background_cancel_run()}
              </button>
            </>
          )}
          {run.run_thread_id && (
            <button
              onClick={onOpenThread}
              title={m.background_open_thread()}
              className="p-1 text-muted-foreground hover:text-foreground"
            >
              <ExternalLink className="w-3 h-3" />
            </button>
          )}
        </div>
      </div>
      {expanded && hasDetail && (
        <pre className="mx-2 mb-2 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/30 p-2 text-[10px] text-muted-foreground">
          {run.summary}
        </pre>
      )}
    </li>
  );
}

/** Resolve uma run pausada em HITL (approve/reject/cancel) via o endpoint. */
async function resolveRun(
  threadId: string,
  runId: string,
  decision: "approve" | "reject" | "cancel",
): Promise<void> {
  await fetch(
    `/sessions/${encodeURIComponent(threadId)}/background/runs/${encodeURIComponent(
      runId,
    )}/resume`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    },
  );
}

export function TasksTab({ threadId }: { threadId: string }) {
  const navigate = useNavigate();
  const workspace = useWorkspacesStore((s) => s.getActive());
  const taskNotifications = useSettingsStore((s) => s.taskNotifications);
  const {
    tasks,
    runs,
    loading,
    refetch,
    createTask,
    toggleTask,
    deleteTask,
    runTask,
  } = useBackgroundTasks(threadId);

  const [showForm, setShowForm] = useState(false);
  const [draft, setDraft] = useState<DraftState>(EMPTY_DRAFT);
  const [submitting, setSubmitting] = useState(false);

  // Revalida ao receber um evento de run em segundo plano via SSE.
  const onWebhook = useCallback(
    (evt: { provider: string }) => {
      if (evt.provider === "background") {
        void refetch();
        if (taskNotifications) {
          useToastStore.getState().info(m.background_tasks_title());
        }
      }
    },
    [refetch, taskNotifications],
  );
  useWebhookEvents(onWebhook);

  const handleCreate = async () => {
    if (!draft.name.trim() || !draft.instruction.trim()) return;
    setSubmitting(true);
    try {
      const trigger_config: Record<string, unknown> =
        draft.trigger_type === "interval"
          ? { cron_expr: draft.cron_expr.trim() }
          : draft.trigger_type === "webhook"
            ? {
                provider: draft.provider.trim(),
                events: draft.events
                  .split(",")
                  .map((e) => e.trim())
                  .filter(Boolean),
              }
            : {};
      const input: CreateTaskInput = {
        kind: draft.kind,
        name: draft.name.trim(),
        instruction: draft.instruction.trim(),
        trigger_type: draft.trigger_type,
        trigger_config,
        workspace_id: workspace?.id ?? null,
      };
      const ok = await createTask(input);
      if (ok) {
        setDraft(EMPTY_DRAFT);
        setShowForm(false);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const runsByTask = useMemo(() => {
    const map = new Map<string, number>();
    for (const r of runs) map.set(r.task_id, (map.get(r.task_id) ?? 0) + 1);
    return map;
  }, [runs]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground">
        <Loader2 className="w-4 h-4 animate-spin" />
      </div>
    );
  }

  const allEmpty = tasks.length === 0 && runs.length === 0;

  return (
    <div className="relative flex h-full flex-col text-xs">
      <div className="flex justify-start px-3 pt-3 pb-2 shrink-0 border-b border-border/40">
        <button
          onClick={() => setShowForm((v) => !v)}
          className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
          data-testid="background-new-task"
        >
          <Plus className="w-3 h-3" />
          {m.background_new_task()}
        </button>
      </div>

      <WorkbenchDialog
        open={showForm}
        onOpenChange={setShowForm}
        title={m.background_new_task()}
        description={m.background_new_task()}
        testId="tasks-form-panel"
      >
        <div className="min-w-0 space-y-2 p-4">
          <div className="flex gap-2">
            <select
              value={draft.kind}
              onChange={(e) =>
                setDraft({ ...draft, kind: e.target.value as BackgroundKind })
              }
              className="flex-1 bg-background border border-border/60 rounded px-1 py-1"
              aria-label={m.background_kind()}
            >
              <option value="routine">{m.background_kind_routine()}</option>
              <option value="heartbreak">
                {m.background_kind_heartbreak()}
              </option>
            </select>
            <select
              value={draft.trigger_type}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  trigger_type: e.target.value as TriggerType,
                })
              }
              className="flex-1 bg-background border border-border/60 rounded px-1 py-1"
              aria-label={m.background_trigger()}
            >
              <option value="interval">
                {m.background_trigger_interval()}
              </option>
              <option value="webhook">{m.background_trigger_webhook()}</option>
              <option value="manual">{m.background_trigger_manual()}</option>
            </select>
          </div>
          <input
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            placeholder={m.background_field_name()}
            className="w-full bg-background border border-border/60 rounded px-2 py-1"
          />
          <textarea
            value={draft.instruction}
            onChange={(e) =>
              setDraft({ ...draft, instruction: e.target.value })
            }
            placeholder={m.background_field_instruction()}
            rows={2}
            className="w-full bg-background border border-border/60 rounded px-2 py-1 resize-none"
          />
          {draft.trigger_type === "interval" && (
            <input
              value={draft.cron_expr}
              onChange={(e) =>
                setDraft({ ...draft, cron_expr: e.target.value })
              }
              placeholder={m.background_field_cron()}
              className="w-full bg-background border border-border/60 rounded px-2 py-1 font-mono"
            />
          )}
          {draft.trigger_type === "webhook" && (
            <div className="flex gap-2">
              <input
                value={draft.provider}
                onChange={(e) =>
                  setDraft({ ...draft, provider: e.target.value })
                }
                placeholder={m.background_field_provider()}
                className="flex-1 bg-background border border-border/60 rounded px-2 py-1"
              />
              <input
                value={draft.events}
                onChange={(e) => setDraft({ ...draft, events: e.target.value })}
                placeholder={m.background_field_events()}
                className="flex-1 bg-background border border-border/60 rounded px-2 py-1"
              />
            </div>
          )}
          <div className="flex justify-end gap-2">
            <button
              onClick={() => setShowForm(false)}
              className="px-2 py-1 rounded text-muted-foreground hover:bg-muted/50"
            >
              {m.background_cancel()}
            </button>
            <button
              onClick={() => void handleCreate()}
              disabled={submitting}
              className="px-2 py-1 rounded bg-primary text-primary-foreground disabled:opacity-50"
              data-testid="background-create"
            >
              {submitting ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                m.background_create()
              )}
            </button>
          </div>
        </div>
      </WorkbenchDialog>

      {allEmpty ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 p-4 text-center">
          <ListTodo className="w-6 h-6 text-muted-foreground/40" />
          <p className="text-xs text-muted-foreground">
            {m.background_empty()}
          </p>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto min-h-0 px-3 pt-3 pb-3 space-y-4">
          {tasks.length === 0 ? null : (
            <ul className="space-y-2" data-testid="background-task-list">
              {tasks.map((t) => (
                <li
                  key={t.id}
                  className="border border-border/60 rounded-md p-2 space-y-1"
                  data-testid="background-task-item"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium truncate">{t.name}</span>
                    <div className="flex items-center gap-1 shrink-0">
                      {t.kind !== "subagent" && (
                        <button
                          onClick={() => void runTask(t.id)}
                          title={m.background_run_now()}
                          className="p-1 text-muted-foreground hover:text-foreground"
                          data-testid="background-run-now"
                        >
                          <Play className="w-3 h-3" />
                        </button>
                      )}
                      <button
                        onClick={() => void deleteTask(t.id)}
                        title={m.background_delete()}
                        className="p-1 text-muted-foreground hover:text-destructive"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                      {t.kind !== "subagent" && (
                        <Switch
                          checked={t.enabled}
                          onCheckedChange={(v) => void toggleTask(t.id, v)}
                          aria-label={
                            t.enabled
                              ? m.background_active()
                              : m.background_paused()
                          }
                        />
                      )}
                    </div>
                  </div>
                  <p className="text-muted-foreground truncate">
                    {t.instruction}
                  </p>
                  <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                    <span className="px-1 rounded bg-muted/60">
                      {t.kind === "routine"
                        ? m.background_kind_routine()
                        : t.kind === "heartbreak"
                          ? m.background_kind_heartbreak()
                          : m.background_trigger_subagent()}
                    </span>
                    <span className="px-1 rounded bg-muted/60">
                      {t.trigger_type === "interval"
                        ? m.background_trigger_interval()
                        : t.trigger_type === "webhook"
                          ? m.background_trigger_webhook()
                          : t.trigger_type === "subagent"
                            ? m.background_trigger_subagent()
                            : m.background_trigger_manual()}
                    </span>
                    <span>
                      {m.background_last_run()}:{" "}
                      {t.last_run_at ?? m.background_never()}
                    </span>
                    {runsByTask.has(t.id) && (
                      <span>· {runsByTask.get(t.id)}×</span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}

          <div className="space-y-2 border-t border-border/40 pt-3">
            <span className="font-medium">{m.background_runs_title()}</span>
            {runs.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-1 py-4 text-center">
                <p className="text-muted-foreground">
                  {m.background_no_runs()}
                </p>
              </div>
            ) : (
              <ul className="space-y-1" data-testid="background-run-list">
                {runs.map((r) => (
                  <RunItem
                    key={r.id}
                    run={r}
                    onResolve={(decision) =>
                      void resolveRun(threadId, r.id, decision).then(() =>
                        refetch(),
                      )
                    }
                    onOpenThread={() =>
                      void navigate({
                        to: "/session/$threadId",
                        params: { threadId: r.run_thread_id as string },
                      })
                    }
                  />
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

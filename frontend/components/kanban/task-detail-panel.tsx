"use client";

/**
 * Painel de detalhe de um card do Kanban. Menu de status
 * (só alvos legais de `DRAG_TRANSITIONS`), aprovar/reprovar review,
 * assignee editável, progresso de subtasks, dependências editáveis, e
 * comentários + timeline de transições (já existentes).
 *
 * Fetch sob demanda ao abrir, mesmo padrão que `TaskCard` já usa pro
 * histórico de execuções — sem carregar nada enquanto o painel está
 * fechado.
 */

import { useEffect, useState } from "react";

import { m } from "@/lib/paraglide/messages";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import {
  applyDragTransition,
  DRAG_TRANSITIONS,
  type KanbanTask,
} from "./kanban-board";

interface TaskComment {
  id: string;
  user_id: string;
  body: string;
  created_at: string;
}

interface TaskEvent {
  id: string;
  from_status: string | null;
  to_status: string;
  block_kind: string | null;
  block_reason: string | null;
  created_at: string;
}

interface AgentProfileOption {
  id: string;
  name: string;
}

//: Espelha `COLUNAS` de `kanban-board.tsx` — sem reexportar o array por
//: inteiro (é interno ao módulo do board), só os rótulos que o menu de
//: status do drawer também precisa.
const STATUS_LABELS: Record<string, () => string> = {
  triage: () => m.kanban_column_triage(),
  todo: () => m.kanban_column_todo(),
  scheduled: () => m.kanban_column_scheduled(),
  ready: () => m.kanban_column_ready(),
  running: () => m.kanban_column_running(),
  blocked: () => m.kanban_column_blocked(),
  review: () => m.kanban_column_review(),
  done: () => m.kanban_column_done(),
  archived: () => m.kanban_column_archived(),
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status]?.() ?? status;
}

export function TaskDetailPanel({
  threadId,
  task,
  open,
  onOpenChange,
  onChanged,
}: {
  threadId: string;
  task: KanbanTask;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChanged: () => void;
}) {
  const base = `/sessions/${threadId}/background/tasks/${task.id}`;
  const [comments, setComments] = useState<TaskComment[] | null>(null);
  const [events, setEvents] = useState<TaskEvent[] | null>(null);
  const [novoComentario, setNovoComentario] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [profiles, setProfiles] = useState<AgentProfileOption[]>([]);
  const [novoParentId, setNovoParentId] = useState("");
  const [erroLink, setErroLink] = useState<string | null>(null);

  const carregar = () => {
    void fetch(`${base}/comments`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => setComments(Array.isArray(data) ? data : []))
      .catch(() => setComments([]));
    void fetch(`${base}/events`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => setEvents(Array.isArray(data) ? data : []))
      .catch(() => setEvents([]));
  };

  useEffect(() => {
    if (open) carregar();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, task.id]);

  useEffect(() => {
    if (!open) return;
    void fetch("/agent-profiles", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => setProfiles(Array.isArray(data) ? data : []))
      .catch(() => setProfiles([]));
  }, [open]);

  const enviarComentario = () => {
    const corpo = novoComentario.trim();
    if (!corpo) return;
    setEnviando(true);
    setErro(null);
    void fetch(`${base}/comments`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body: corpo }),
    })
      .then((r) => {
        if (!r.ok) throw new Error("falha ao comentar");
        setNovoComentario("");
        carregar();
      })
      .catch(() => setErro(m.kanban_comment_error()))
      .finally(() => setEnviando(false));
  };

  const mudarStatus = (target: string) => {
    if (!target || target === task.status) return;
    void applyDragTransition(threadId, task, target).then((aplicado) => {
      if (aplicado) onChanged();
    });
  };

  const aprovarReview = () => {
    void fetch(`${base}/review/approve`, {
      method: "POST",
      credentials: "include",
    }).then((r) => {
      if (r.ok) onChanged();
    });
  };

  const mudarAssignee = (agentProfileId: string) => {
    void fetch(base, {
      method: "PATCH",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_profile_id: agentProfileId || null }),
    }).then((r) => {
      if (r.ok) onChanged();
    });
  };

  const adicionarDependencia = () => {
    const parentId = novoParentId.trim();
    if (!parentId) return;
    setErroLink(null);
    void fetch(`${base}/links`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parent_id: parentId }),
    }).then((r) => {
      if (!r.ok) {
        setErroLink(m.kanban_link_error());
        return;
      }
      setNovoParentId("");
      onChanged();
    });
  };

  const removerDependencia = (parentId: string) => {
    void fetch(`${base}/links/${parentId}`, {
      method: "DELETE",
      credentials: "include",
    }).then((r) => {
      if (r.ok) onChanged();
    });
  };

  const alvosDeStatus = DRAG_TRANSITIONS[task.status] ?? [];

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full max-w-md p-6 overflow-y-auto">
        <h2 className="text-sm font-semibold mb-4">{task.name}</h2>

        <section className="mb-6 grid grid-cols-2 gap-3">
          <div>
            <label
              htmlFor="kanban-detail-status"
              className="text-[10px] uppercase text-muted-foreground"
            >
              {m.kanban_detail_status_label()}
            </label>
            <select
              id="kanban-detail-status"
              aria-label={m.kanban_detail_status_label()}
              value={task.status}
              onChange={(e) => mudarStatus(e.target.value)}
              className="w-full rounded border bg-background px-2 py-1 text-xs"
              disabled={alvosDeStatus.length === 0}
            >
              <option value={task.status}>{statusLabel(task.status)}</option>
              {alvosDeStatus.map((s) => (
                <option key={s} value={s}>
                  {statusLabel(s)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label
              htmlFor="kanban-detail-assignee"
              className="text-[10px] uppercase text-muted-foreground"
            >
              {m.kanban_task_assignee()}
            </label>
            <select
              id="kanban-detail-assignee"
              aria-label={m.kanban_task_assignee()}
              value={task.agent_profile_id ?? ""}
              onChange={(e) => mudarAssignee(e.target.value)}
              className="w-full rounded border bg-background px-2 py-1 text-xs"
            >
              <option value="">{m.kanban_task_assignee_none()}</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
        </section>

        {task.status === "review" && (
          <section className="mb-6 flex items-center gap-2">
            <button
              onClick={aprovarReview}
              className="text-xs px-2 py-1 rounded bg-primary text-primary-foreground"
            >
              {m.kanban_review_approve()}
            </button>
            <button
              onClick={() => mudarStatus("ready")}
              className="text-xs px-2 py-1 rounded text-muted-foreground hover:underline"
            >
              {m.kanban_review_reject()}
            </button>
          </section>
        )}

        {task.progress && (
          <section className="mb-6">
            <h3 className="text-xs font-medium uppercase text-muted-foreground mb-2">
              {m.kanban_progress_title()}
            </h3>
            <div className="flex items-center gap-2">
              <div className="h-1.5 flex-1 rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full rounded-full bg-primary"
                  style={{
                    width: `${Math.round((task.progress.done / task.progress.total) * 100)}%`,
                  }}
                />
              </div>
              <span className="text-[10px] text-muted-foreground">
                {task.progress.done}/{task.progress.total}
              </span>
            </div>
          </section>
        )}

        <section className="mb-6">
          <h3 className="text-xs font-medium uppercase text-muted-foreground mb-2">
            {m.kanban_dependencies_title()}
          </h3>
          <ul className="space-y-1 mb-2">
            {(task.dependencies ?? []).length === 0 ? (
              <li className="text-xs text-muted-foreground">
                {m.kanban_dependencies_empty()}
              </li>
            ) : (
              (task.dependencies ?? []).map((d) => (
                <li
                  key={d.id}
                  className="flex items-center justify-between text-xs"
                >
                  <span>
                    {d.name}{" "}
                    <span className="text-muted-foreground">
                      ({statusLabel(d.status)})
                    </span>
                  </span>
                  <button
                    onClick={() => removerDependencia(d.id)}
                    aria-label={m.kanban_dependency_remove()}
                    className="text-muted-foreground hover:text-destructive"
                  >
                    ×
                  </button>
                </li>
              ))
            )}
          </ul>
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={novoParentId}
              onChange={(e) => setNovoParentId(e.target.value)}
              placeholder={m.kanban_dependency_add_placeholder()}
              aria-label={m.kanban_dependency_add_placeholder()}
              className="flex-1 rounded border bg-background px-2 py-1 text-xs"
            />
            <button
              onClick={adicionarDependencia}
              className="text-xs px-2 py-1 rounded bg-primary text-primary-foreground"
            >
              {m.kanban_dependency_add()}
            </button>
          </div>
          {erroLink && (
            <span className="text-[10px] text-destructive">{erroLink}</span>
          )}
        </section>

        <section className="mb-6">
          <h3 className="text-xs font-medium uppercase text-muted-foreground mb-2">
            {m.kanban_comments_title()}
          </h3>
          <ScrollArea className="max-h-56 mb-2">
            <ul className="space-y-2 pr-2">
              {comments === null ? null : comments.length === 0 ? (
                <li className="text-xs text-muted-foreground">
                  {m.kanban_comment_empty()}
                </li>
              ) : (
                comments.map((c) => (
                  <li key={c.id} className="text-xs">
                    <p className="text-muted-foreground">
                      {c.user_id} · {new Date(c.created_at).toLocaleString()}
                    </p>
                    <p>{c.body}</p>
                  </li>
                ))
              )}
            </ul>
          </ScrollArea>
          <Textarea
            value={novoComentario}
            onChange={(e) => setNovoComentario(e.target.value)}
            placeholder={m.kanban_comment_placeholder()}
            aria-label={m.kanban_comment_placeholder()}
            className="text-xs min-h-[60px]"
          />
          <div className="mt-2 flex items-center gap-2">
            <button
              onClick={enviarComentario}
              disabled={enviando || !novoComentario.trim()}
              className="text-xs px-2 py-1 rounded bg-primary text-primary-foreground disabled:opacity-50"
            >
              {m.kanban_comment_submit()}
            </button>
            {erro && (
              <span className="text-[10px] text-destructive">{erro}</span>
            )}
          </div>
        </section>

        <section>
          <h3 className="text-xs font-medium uppercase text-muted-foreground mb-2">
            {m.kanban_events_title()}
          </h3>
          <ScrollArea className="max-h-56">
            <ul className="space-y-1.5 pr-2 text-xs">
              {events === null ? null : events.length === 0 ? (
                <li className="text-muted-foreground">
                  {m.kanban_events_empty()}
                </li>
              ) : (
                events.map((e) => (
                  <li key={e.id} className="text-muted-foreground">
                    <span>
                      {e.from_status ?? "—"} → {e.to_status}
                    </span>{" "}
                    · {new Date(e.created_at).toLocaleString()}
                    {e.block_reason && (
                      <p className="text-[10px]">{e.block_reason}</p>
                    )}
                  </li>
                ))
              )}
            </ul>
          </ScrollArea>
        </section>
      </SheetContent>
    </Sheet>
  );
}

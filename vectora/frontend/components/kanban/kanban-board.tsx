"use client";

/**
 * Board do 3º modo de interface — feature pública, sem gate de flag.
 *
 * Cinco colunas fixas, sempre visíveis (mesmo com 0 tasks — colunas vazias
 * não são bug, são o estado normal de um board recém-criado). `triage` fica
 * fora das colunas principais (dropzone própria, ver `TriageDropzone`);
 * `archived` só aparece quando o filtro "mostrar arquivadas" está ativo.
 *
 * Drag-and-drop só nas transições seguras (`DRAG_TRANSITIONS`), reaproveitando
 * os mesmos endpoints dos botões explícitos — nunca uma chamada nova. `*→running`
 * (exclusivo do claim atômico do scheduler) e `*→done` (só a run terminando de
 * verdade decide) nunca disparam chamada nenhuma: o board os recusa antes de
 * qualquer fetch, e o backend (`kanban.manual_transition`) recusaria de novo se
 * o frontend tentasse.
 */

import { useEffect, useRef, useState } from "react";
import {
  DndContext,
  useDraggable,
  useDroppable,
  type DragEndEvent,
} from "@dnd-kit/core";

import { m } from "@/lib/paraglide/messages";
import "./kanban.css";
import { TaskDetailPanel } from "./task-detail-panel";

//: Enum real do backend (`background_tasks.py::VALID_PRIORITIES`) — não um
//: número livre como no Hermes. Vectora não tem esse conceito; um campo
//: numérico aqui só produziria valores que o backend rejeitaria com 400.
const PRIORITIES = ["low", "normal", "high", "urgent"] as const;

interface WorkspaceOption {
  id: string;
  name: string;
}

interface AgentProfileOption {
  id: string;
  name: string;
}

function NewTaskForm({
  threadId,
  boardId,
  onCreated,
}: {
  threadId: string;
  //: Board ativo — task nova entra associada a ele quando um board está
  //: selecionado. `null` na visão por session, comportamento sem board
  //: inalterado.
  boardId: string | null;
  onCreated: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [instruction, setInstruction] = useState("");
  const [priority, setPriority] = useState("normal");
  const [workspaceId, setWorkspaceId] = useState("");
  const [agentProfileId, setAgentProfileId] = useState("");
  const [workspaces, setWorkspaces] = useState<WorkspaceOption[]>([]);
  const [profiles, setProfiles] = useState<AgentProfileOption[]>([]);

  useEffect(() => {
    if (!open) return;
    void fetch("/workspaces", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : { workspaces: [] }))
      .then((data) => setWorkspaces(data.workspaces ?? []))
      .catch(() => setWorkspaces([]));
    void fetch("/agent-profiles", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => setProfiles(Array.isArray(data) ? data : []))
      .catch(() => setProfiles([]));
  }, [open]);

  const criar = () => {
    if (!name.trim() || !instruction.trim()) return;
    void fetch(`/sessions/${threadId}/background/tasks`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: "routine",
        name: name.trim(),
        instruction: instruction.trim(),
        trigger_type: "manual",
        priority,
        workspace_id: workspaceId || null,
        agent_profile_id: agentProfileId || null,
        board_id: boardId,
      }),
    }).then(() => {
      setName("");
      setInstruction("");
      setPriority("normal");
      setWorkspaceId("");
      setAgentProfileId("");
      setOpen(false);
      onCreated();
    });
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="text-xs text-primary hover:underline mb-2"
      >
        {m.kanban_new_task()}
      </button>
    );
  }

  return (
    <div className="mb-3 max-w-sm rounded-md border bg-card p-3 space-y-2">
      <div>
        <label
          htmlFor="kanban-new-name"
          className="text-[10px] text-muted-foreground"
        >
          {m.kanban_task_name()}
        </label>
        <input
          id="kanban-new-name"
          aria-label={m.kanban_task_name()}
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full rounded border bg-background px-2 py-1 text-xs"
        />
      </div>
      <div>
        <label
          htmlFor="kanban-new-instruction"
          className="text-[10px] text-muted-foreground"
        >
          {m.kanban_task_instruction()}
        </label>
        <textarea
          id="kanban-new-instruction"
          aria-label={m.kanban_task_instruction()}
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          className="w-full rounded border bg-background px-2 py-1 text-xs min-h-[60px]"
        />
      </div>
      <div className="grid grid-cols-3 gap-2">
        <div>
          <label
            htmlFor="kanban-new-priority"
            className="text-[10px] text-muted-foreground"
          >
            {m.kanban_task_priority()}
          </label>
          <select
            id="kanban-new-priority"
            aria-label={m.kanban_task_priority()}
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
            className="w-full rounded border bg-background px-2 py-1 text-xs"
          >
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label
            htmlFor="kanban-new-workspace"
            className="text-[10px] text-muted-foreground"
          >
            {m.kanban_task_workspace()}
          </label>
          <select
            id="kanban-new-workspace"
            aria-label={m.kanban_task_workspace()}
            value={workspaceId}
            onChange={(e) => setWorkspaceId(e.target.value)}
            className="w-full rounded border bg-background px-2 py-1 text-xs"
          >
            <option value="">{m.kanban_task_workspace_none()}</option>
            {workspaces.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label
            htmlFor="kanban-new-assignee"
            className="text-[10px] text-muted-foreground"
          >
            {m.kanban_task_assignee()}
          </label>
          <select
            id="kanban-new-assignee"
            aria-label={m.kanban_task_assignee()}
            value={agentProfileId}
            onChange={(e) => setAgentProfileId(e.target.value)}
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
      </div>
      <div className="flex gap-2">
        <button
          onClick={criar}
          className="text-xs px-2 py-1 rounded bg-primary text-primary-foreground"
        >
          {m.kanban_create()}
        </button>
        <button
          onClick={() => setOpen(false)}
          className="text-xs px-2 py-1 rounded text-muted-foreground hover:underline"
        >
          {m.kanban_action_cancel()}
        </button>
      </div>
    </div>
  );
}

//: Switcher de board — "Board da sessão" (null) é a visão sem board
//: selecionado, sempre disponível mesmo sem nenhum board criado ainda.
//: Criar um board novo seleciona ele na hora, sem passo extra.
function BoardSwitcher({
  boards,
  activeBoardId,
  onChange,
  onCreated,
}: {
  boards: KanbanBoardOption[];
  activeBoardId: string | null;
  onChange: (boardId: string) => void;
  onCreated: (board: KanbanBoardOption) => void;
}) {
  const [criando, setCriando] = useState(false);
  const [novoNome, setNovoNome] = useState("");

  const criar = () => {
    const nome = novoNome.trim();
    if (!nome) return;
    void fetch("/boards", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: nome }),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((board) => {
        if (!board) return;
        onCreated(board);
        setNovoNome("");
        setCriando(false);
      });
  };

  return (
    <div className="mb-3 flex items-center gap-2">
      <select
        value={activeBoardId ?? ""}
        onChange={(e) => onChange(e.target.value)}
        aria-label={m.kanban_board_switcher_label()}
        className="rounded border bg-background px-2 py-1 text-xs"
      >
        <option value="">{m.kanban_board_session_view()}</option>
        {boards.map((b) => (
          <option key={b.id} value={b.id}>
            {b.name}
          </option>
        ))}
      </select>
      {criando ? (
        <>
          <input
            type="text"
            value={novoNome}
            onChange={(e) => setNovoNome(e.target.value)}
            placeholder={m.kanban_board_new_placeholder()}
            aria-label={m.kanban_board_new_placeholder()}
            className="w-32 rounded border bg-background px-2 py-1 text-xs"
          />
          <button
            onClick={criar}
            className="text-xs px-2 py-1 rounded bg-primary text-primary-foreground"
          >
            {m.kanban_create()}
          </button>
          <button
            onClick={() => {
              setCriando(false);
              setNovoNome("");
            }}
            className="text-xs text-muted-foreground hover:underline"
          >
            {m.kanban_action_cancel()}
          </button>
        </>
      ) : (
        <button
          onClick={() => setCriando(true)}
          className="text-xs text-primary hover:underline"
        >
          {m.kanban_board_new()}
        </button>
      )}
    </div>
  );
}

//: Atualização em tempo real vem por push (SSE, ver `useKanbanSse` abaixo).
//: Este polling é só reconciliação de baixa frequência — cobre o evento
//: perdido numa reconexão de rede — não a via principal de atualização.
const POLL_INTERVAL_MS = 30000;

//: Canal SSE genérico de webhooks (`backend/api/handlers/webhooks.py`),
//: reaproveitado em vez de um endpoint dedicado ao Kanban — o provider
//: `"kanban"` no payload é o que distingue estes eventos dos demais.
const SSE_URL = "/webhook/events";
const SSE_RECONNECT_DELAY_MS = 3000;

export interface KanbanDependency {
  id: string;
  name: string;
  status: string;
}

export interface KanbanTask {
  id: string;
  name: string;
  status: string;
  block_kind: string | null;
  block_reason: string | null;
  //: Pais diretos (`vectora_task_links`) com status atual — substitui o
  //: badge de texto solto por um contador N/M real, direto do backend.
  dependencies?: KanbanDependency[];
  //: "tenant" do card — já existia no backend (`workspace_id`), só não
  //: chegava até aqui.
  workspace_id?: string | null;
  //: "assignee" do card — perfil de agente atribuído.
  agent_profile_id?: string | null;
  //: "low" | "normal" | "high" | "urgent" — sinal visual, não afeta ordem
  //: real de claim.
  priority?: string;
  //: `None` sem claim ativo. Fonte real do arc animado — deriva se o
  //: heartbeat (backend/scheduling/background_tasks.py, watchdog de 60s)
  //: está fresco ou parou.
  claim_expires_at?: string | null;
  //: Rollup de subtasks diretas (`kanban_decompose`). `undefined`/`null`
  //: — não `{done:0,total:0}` — quando a task não tem subtask nenhuma.
  progress?: { done: number; total: number } | null;
}

//: Mesmos valores de `backend/scheduling/kanban.py::_DEFAULT_CLAIM_TTL_S`
//: e `backend/scheduling/background_tasks.py::_HEARTBEAT_INTERVAL_S` — o
//: arc deriva "heartbeat fresco vs parado" de `claim_expires_at` sozinho
//: (o backend não expõe o timestamp do último heartbeat em si), então
//: esses dois números precisam ficar em sincronia com o backend.
const CLAIM_TTL_S = 900;
const ARC_STALE_AFTER_S = 120;

export type KanbanArcState = "running" | "stale" | "queued" | "none";

/** Estado do arc animado do card. Só entra depois do heartbeat real: sem
 * `claim_expires_at`, uma task `running` cai em "stale" (arco parado
 * avisando que não há prova de vida), nunca em "running" — decoração sem
 * dado por trás é exatamente a classe de problema que este cálculo
 * evita. */
export function arcState(task: KanbanTask): KanbanArcState {
  if (task.status === "running") {
    if (!task.claim_expires_at) return "stale";
    const remainingS =
      (new Date(task.claim_expires_at).getTime() - Date.now()) / 1000;
    const sinceHeartbeatS = CLAIM_TTL_S - remainingS;
    return sinceHeartbeatS > ARC_STALE_AFTER_S ? "stale" : "running";
  }
  if (task.status === "review" || task.status === "triage") return "queued";
  if (task.status === "ready" && task.agent_profile_id) return "queued";
  return "none";
}

const PRIORITY_CLASS: Record<string, string> = {
  low: "text-muted-foreground",
  normal: "text-muted-foreground",
  high: "text-amber-600 dark:text-amber-400",
  urgent: "text-destructive",
};

interface KanbanSseEventData {
  task_id: string;
  //: `None` pra task sem board. Presente mesmo na visão por session (não
  //: só na visão por board), então a checagem de relevância do evento é
  //: sempre a mesma código, os dois modos.
  board_id?: string | null;
  status: string;
  block_kind: string | null;
  block_reason: string | null;
}

interface KanbanBoardOption {
  id: string;
  slug: string;
  name: string;
}

//: Chave de persistência do board ativo — por thread, já que o board
//: escolhido numa sessão não deveria "vazar" pra outra.
function boardStorageKey(threadId: string): string {
  return `vectora-kanban-active-board-${threadId}`;
}

interface WebhookSseEvent {
  type: string;
  provider: string;
  event_type: string;
  data: KanbanSseEventData;
}

//: Espelha `backend/scheduling/kanban.py::KANBAN_STATUSES` menos `archived`,
//: que entra por filtro. Toda tarefa precisa de uma coluna: sem `triage`,
//: `scheduled` e `review` aqui, tarefas nesses status eram descartadas pelo
//: filtro de visibilidade e sumiam do board sem nenhum aviso.
//:
//: `tone` é o token `--kanban-tone-*` (definido em `src/styles.css`) que
//: alimenta a borda esquerda do card e o dot do header da coluna — um mapa,
//: dois consumidores (a cor do arc soma um terceiro).
const COLUNAS: { status: string; label: () => string; tone: string }[] = [
  { status: "triage", label: () => m.kanban_column_triage(), tone: "triage" },
  { status: "todo", label: () => m.kanban_column_todo(), tone: "todo" },
  {
    status: "scheduled",
    label: () => m.kanban_column_scheduled(),
    tone: "scheduled",
  },
  { status: "ready", label: () => m.kanban_column_ready(), tone: "ready" },
  {
    status: "running",
    label: () => m.kanban_column_running(),
    tone: "running",
  },
  {
    status: "blocked",
    label: () => m.kanban_column_blocked(),
    tone: "blocked",
  },
  { status: "review", label: () => m.kanban_column_review(), tone: "review" },
  { status: "done", label: () => m.kanban_column_done(), tone: "done" },
];

//: Coluna de escape pra status que o backend passe a emitir e o frontend
//: ainda não conheça — melhor uma lane "Outros" do que a tarefa evaporar.
const FALLBACK_STATUS = "__other__";

function kanbanToneVar(status: string): string {
  const tone = COLUNAS.find((c) => c.status === status)?.tone ?? "archived";
  return `var(--color-kanban-tone-${tone})`;
}

//: Pares acionáveis por drag-and-drop — espelha
//: `backend/scheduling/kanban.py::MANUAL_TRANSITIONS`. `running` e `done`
//: nunca aparecem como alvo: são exclusivos do claim atômico do scheduler e
//: da run terminando de verdade, respectivamente. O backend recusa de novo
//: se este mapa algum dia divergir — esta cópia é só pra recusar o drop
//: antes de qualquer chamada de rede.
//: Exportado pro menu de status do drawer reusar exatamente os mesmos
//: alvos legais que o drag-and-drop já valida — um mapa só, dois
//: consumidores.
export const DRAG_TRANSITIONS: Record<string, string[]> = {
  todo: ["ready", "triage"],
  ready: ["triage"],
  blocked: ["ready"],
  //: Reprovar review — devolve pro fluxo ativo. `review→done`
  //: nunca entra aqui de propósito: é o endpoint dedicado de aprovação
  //: (`/review/approve`), que registra quem aprovou — não um `PATCH`
  //: genérico de drag-and-drop.
  review: ["ready"],
  //: Reabertura manual de uma task já concluída.
  done: ["review"],
};

function isDragAllowed(from: string, to: string): boolean {
  return (DRAG_TRANSITIONS[from] ?? []).includes(to);
}

async function patchStatus(
  threadId: string,
  taskId: string,
  status: string,
): Promise<void> {
  await fetch(`/sessions/${threadId}/background/tasks/${taskId}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
}

async function unblockTaskRequest(
  threadId: string,
  taskId: string,
): Promise<void> {
  await fetch(`/sessions/${threadId}/background/tasks/${taskId}/unblock`, {
    method: "POST",
    credentials: "include",
  });
}

async function bulkArchive(threadId: string, taskIds: string[]): Promise<void> {
  await fetch(`/sessions/${threadId}/background/tasks/bulk`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task_ids: taskIds, action: "archive" }),
  });
}

/** Aplica (ou recusa) uma transição de drag-and-drop. `false` sem chamada de
 * rede nenhuma quando o par não está em `DRAG_TRANSITIONS` — o card volta
 * pro lugar porque o estado local nunca mudou. `blocked→ready` reaproveita o
 * mesmo endpoint do botão "Desbloquear"; os demais pares usam `PATCH` de
 * status, mesma rota que valida a transição em `kanban.manual_transition`. */
export async function applyDragTransition(
  threadId: string,
  task: KanbanTask,
  targetStatus: string,
): Promise<boolean> {
  if (targetStatus === task.status) return false;
  if (!isDragAllowed(task.status, targetStatus)) return false;
  if (task.status === "blocked" && targetStatus === "ready") {
    await unblockTaskRequest(threadId, task.id);
  } else {
    await patchStatus(threadId, task.id, targetStatus);
  }
  return true;
}

interface KanbanRun {
  id: string;
  status: string;
  trigger_source: string;
  started_at: string;
  finished_at: string | null;
}

function TaskCard({
  task,
  threadId,
  selected,
  onToggleSelect,
  onChanged,
}: {
  task: KanbanTask;
  threadId: string;
  selected: boolean;
  onToggleSelect: (shiftKey: boolean) => void;
  onChanged: () => void;
}) {
  const base = `/sessions/${threadId}/background/tasks/${task.id}`;
  const { attributes, listeners, setNodeRef, transform, isDragging } =
    useDraggable({ id: task.id });
  const [runs, setRuns] = useState<KanbanRun[] | null>(null);
  const [runsOpen, setRunsOpen] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);

  const toggleRuns = () => {
    if (runsOpen) {
      setRunsOpen(false);
      return;
    }
    setRunsOpen(true);
    void fetch(`${base}/runs`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => setRuns(Array.isArray(data) ? data : []))
      .catch(() => setRuns([]));
  };

  const desbloquear = () => {
    void fetch(`${base}/unblock`, {
      method: "POST",
      credentials: "include",
    }).then(onChanged);
  };
  const rodarAgora = () => {
    void fetch(`${base}/run`, { method: "POST", credentials: "include" }).then(
      onChanged,
    );
  };
  const cancelar = () => {
    void fetch(base, { method: "DELETE", credentials: "include" }).then(
      onChanged,
    );
  };

  const style = {
    ...(transform
      ? {
          transform: `translate3d(${transform.x}px, ${transform.y}px, 0)`,
          zIndex: isDragging ? 10 : undefined,
        }
      : undefined),
    borderLeftColor: kanbanToneVar(task.status),
  };

  const arc = arcState(task);
  // Respeito a prefers-reduced-motion já vive no CSS (kanban.css), não
  // aqui — a classe kanban-arc-* entra sempre que o estado pede, o
  // @media da folha de estilo é quem decide se anima ou fica estático.
  const arcClass = arc !== "none" ? `kanban-arc kanban-arc-${arc}` : "";

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`rounded-md border border-l-2 bg-card p-2.5 space-y-1 ${arcClass}`}
    >
      <div className="flex items-start gap-1.5">
        <input
          type="checkbox"
          checked={selected}
          onChange={() => {}}
          onClick={(e) => {
            e.preventDefault();
            onToggleSelect(e.shiftKey);
          }}
          aria-label={m.kanban_select_task()}
          className="mt-0.5 shrink-0"
        />
        <p
          className="text-[0.8125rem] font-medium leading-snug flex-1 cursor-grab active:cursor-grabbing"
          {...attributes}
          {...listeners}
        >
          {task.name}
        </p>
      </div>
      {(task.priority && task.priority !== "normal") ||
      task.workspace_id ||
      task.agent_profile_id ? (
        <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[10px]">
          {task.priority && task.priority !== "normal" && (
            <span
              className={`font-medium uppercase ${PRIORITY_CLASS[task.priority] ?? "text-muted-foreground"}`}
            >
              {task.priority}
            </span>
          )}
          {task.workspace_id && (
            <span className="text-muted-foreground/70">
              {m.kanban_tenant_label({ id: task.workspace_id })}
            </span>
          )}
          {task.agent_profile_id && (
            <span className="text-muted-foreground/70">
              {m.kanban_assignee_label({ id: task.agent_profile_id })}
            </span>
          )}
        </div>
      ) : null}
      {/* Contador N/M real (vectora_task_links via TaskOut.dependencies) —
          substitui o badge de texto solto que a v1 tinha. */}
      {task.dependencies?.length ? (
        <div className="text-[10px] text-muted-foreground">
          <p>
            {m.kanban_dependencies_progress({
              done: task.dependencies.filter((d) => d.status === "done").length,
              total: task.dependencies.length,
            })}
          </p>
          <ul className="pl-2">
            {task.dependencies.map((dep) => (
              <li
                key={dep.id}
                className={
                  dep.status === "done" ? "line-through opacity-60" : ""
                }
              >
                {dep.name}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {task.block_reason ? (
        <p className="text-[10px] text-amber-600 dark:text-amber-400">
          {task.block_reason}
        </p>
      ) : null}
      <div className="flex gap-2 pt-0.5">
        {task.status === "ready" && (
          <button
            onClick={rodarAgora}
            className="text-[10px] text-primary hover:underline"
          >
            {m.kanban_action_run_now()}
          </button>
        )}
        {task.status === "blocked" && (
          <button
            onClick={desbloquear}
            className="text-[10px] text-primary hover:underline"
          >
            {m.kanban_action_unblock()}
          </button>
        )}
        {task.status !== "done" && (
          <button
            onClick={cancelar}
            className="text-[10px] text-muted-foreground hover:underline"
          >
            {m.kanban_action_cancel()}
          </button>
        )}
        <button
          onClick={toggleRuns}
          className="text-[10px] text-muted-foreground hover:underline"
        >
          {runsOpen ? m.kanban_action_hide_runs() : m.kanban_action_show_runs()}
        </button>
        <button
          onClick={() => setDetailOpen(true)}
          className="text-[10px] text-muted-foreground hover:underline"
        >
          {m.kanban_action_view_details()}
        </button>
      </div>
      <TaskDetailPanel
        threadId={threadId}
        task={task}
        open={detailOpen}
        onOpenChange={setDetailOpen}
        onChanged={onChanged}
      />
      {runsOpen ? (
        <ul className="text-[10px] text-muted-foreground space-y-0.5 pl-2">
          {runs === null ? null : runs.length === 0 ? (
            <li>{m.kanban_no_runs()}</li>
          ) : (
            runs.map((run) => (
              <li key={run.id}>
                {run.status} · {run.trigger_source} ·{" "}
                {new Date(run.started_at).toLocaleString()}
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  );
}

function Column({
  status,
  label,
  count,
  collapsed,
  onToggle,
  children,
}: {
  status: string;
  label: string;
  count: number;
  /** Trilho vertical estreito em vez da coluna inteira — lane vazia num
   *  board que tem trabalho em outra coluna. */
  collapsed: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  const tone = kanbanToneVar(status);
  const { setNodeRef, isOver } = useDroppable({ id: status });

  if (collapsed) {
    return (
      <button
        ref={setNodeRef}
        type="button"
        onClick={onToggle}
        aria-label={m.kanban_lane_expand()}
        aria-expanded={false}
        data-testid={`kanban-col-${status}`}
        data-collapsed="true"
        className={`min-h-[12rem] max-h-[26rem] shrink-0 grow-0 basis-9 flex flex-col items-center gap-2 rounded-md border border-border/40 py-2 transition-colors hover:bg-accent/30 ${
          isOver ? "bg-accent/40" : ""
        }`}
      >
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground [writing-mode:vertical-rl]">
          {label}
        </span>
        {count > 0 && (
          <span className="text-[10px] text-muted-foreground">{count}</span>
        )}
      </button>
    );
  }

  return (
    <div
      ref={setNodeRef}
      className={`min-h-[12rem] max-h-[26rem] shrink-0 grow basis-60 flex flex-col gap-2 rounded-md ${
        isOver ? "bg-accent/40" : ""
      }`}
      data-testid={`kanban-col-${status}`}
      data-collapsed="false"
    >
      <div className="flex shrink-0 items-center justify-between px-1">
        <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-muted-foreground">
          <span
            aria-hidden
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ backgroundColor: tone }}
          />
          {label}
        </span>
        <div className="flex items-center gap-1">
          <span className="text-[10px] text-muted-foreground">{count}</span>
          <button
            type="button"
            onClick={onToggle}
            aria-label={m.kanban_lane_collapse()}
            aria-expanded
            className="rounded px-1 text-[10px] text-muted-foreground/60 hover:bg-accent/40 hover:text-foreground"
          >
            ‹
          </button>
        </div>
      </div>
      {/* Rolagem por lane, nunca do board inteiro: com o board rolando, uma
          coluna cheia arrastava as outras junto e o rodapé sumia. */}
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto">
        {count === 0 ? (
          <p className="px-1 text-[10px] text-muted-foreground/60">
            {m.kanban_column_empty()}
          </p>
        ) : (
          children
        )}
      </div>
    </div>
  );
}

//: Alvo de drop pra "esconder/adiar" (`todo`/`ready` → `triage`) — não é uma
//: coluna do board (triage não faz parte do fluxo ativo, ver comentário no
//: topo do arquivo), só uma faixa fina que aceita o drop.
function TriageDropzone() {
  const { setNodeRef, isOver } = useDroppable({ id: "triage" });

  return (
    <div
      ref={setNodeRef}
      className={`mb-2 rounded-md border border-dashed px-2 py-1.5 text-[10px] text-muted-foreground ${
        isOver ? "bg-accent/40 border-primary" : ""
      }`}
    >
      {m.kanban_triage_dropzone()}
    </div>
  );
}

export function KanbanBoard({ threadId }: { threadId: string }) {
  const [tasks, setTasks] = useState<KanbanTask[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lastSelectedId, setLastSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [tenantFilter, setTenantFilter] = useState("");
  const [assigneeFilter, setAssigneeFilter] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  //: Colapso manual por lane, sobrepondo o automático (lane vazia colapsa
  //: sozinha quando o board tem trabalho). Chave ausente = segue o automático.
  const [laneOverrides, setLaneOverrides] = useState<Record<string, boolean>>(
    {},
  );

  // Multi-board. `null` = visão por session (padrão, comportamento sem
  // board inalterado). Persistido por thread: o board escolhido não
  // deveria "vazar" pra outra sessão.
  const [boards, setBoards] = useState<KanbanBoardOption[]>([]);
  const [activeBoardId, setActiveBoardId] = useState<string | null>(() => {
    try {
      return localStorage.getItem(boardStorageKey(threadId));
    } catch {
      return null;
    }
  });

  useEffect(() => {
    void fetch("/boards", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => setBoards(Array.isArray(data) ? data : []))
      .catch(() => setBoards([]));
  }, []);

  const trocarBoard = (boardId: string) => {
    setActiveBoardId(boardId || null);
    try {
      if (boardId) localStorage.setItem(boardStorageKey(threadId), boardId);
      else localStorage.removeItem(boardStorageKey(threadId));
    } catch {
      // localStorage indisponível (modo privado, quota) — só perde a
      // persistência entre sessões, não é motivo pra quebrar a troca.
    }
  };

  const carregar = () => {
    let cancelado = false;
    const url = activeBoardId
      ? `/boards/${activeBoardId}/board`
      : `/sessions/${threadId}/background/tasks`;
    void fetch(url, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : activeBoardId ? { columns: [] } : []))
      .then((data) => {
        if (cancelado) return;
        if (activeBoardId) {
          // `GET /boards/{id}/board` devolve `{columns: [{status, tasks}]}`
          // já agrupado — achata de volta pra lista plana, que é o
          // formato que todo o resto do componente (COLUNAS/colunasAtivas/
          // filtros) já sabe consumir. Reagrupar do zero pra consumir a
          // resposta pré-agrupada seria reescrever a lógica existente só
          // pra fazer a mesma coisa duas vezes.
          const columns = Array.isArray(data?.columns) ? data.columns : [];
          setTasks(
            columns.flatMap((c: { tasks?: unknown[] }) =>
              Array.isArray(c.tasks) ? c.tasks : [],
            ),
          );
        } else {
          // A resposta de /tasks é `list[TaskOut]` (array puro) — não um
          // envelope `{tasks: [...]}`. Formato inesperado degrada pro
          // board vazio em vez de lançar.
          setTasks(Array.isArray(data) ? data : []);
        }
      })
      .catch(() => {
        // Board vazio é melhor que tela de erro — as tarefas seguem
        // rodando, só a visualização não carregou.
      });
    return () => {
      cancelado = true;
    };
  };

  useEffect(carregar, [threadId, activeBoardId]);

  // Ref sempre com a versão mais recente de `carregar` — o interval abaixo
  // só precisa ser recriado quando `threadId` muda, não a cada render.
  const carregarRef = useRef(carregar);
  useEffect(() => {
    carregarRef.current = carregar;
  });

  // Pausa o polling com a aba oculta — evita chamada de fundo desnecessária
  // quando o usuário está em outra aba/app. Agora é só reconciliação de
  // baixa frequência: a atualização principal chega via SSE abaixo. O
  // interval não precisa ser recriado quando `threadId` muda — chama sempre
  // `carregarRef.current()`, que o efeito acima mantém na versão mais
  // recente (já fechada sobre o thread atual).
  useEffect(() => {
    const interval = setInterval(() => {
      if (document.visibilityState === "visible") carregarRef.current();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  // Aplica um evento de mudança de status a um card específico, sem
  // refazer o fetch do board inteiro. Task ainda desconhecida localmente
  // (ex.: criada por outra sessão/processo) cai no fallback de `carregar`.
  const aplicarEventoSse = (data: KanbanSseEventData) => {
    // Visão por board: evento de uma task de OUTRO board não é
    // relevante aqui — nem atualiza card nenhum, nem dispara refetch (que
    // recarregaria a lista certa, mas sem necessidade nenhuma).
    if (activeBoardId && data.board_id !== activeBoardId) return;
    // Checa contra o `tasks` do último render (via ref abaixo), não dentro
    // do updater funcional: `setTasks` não roda o updater de forma
    // síncrona, então ler o resultado logo depois de chamá-lo é uma corrida
    // — o card entra em `carregarRef.current()` mesmo quando já existe.
    if (!tasks.some((t) => t.id === data.task_id)) {
      carregarRef.current();
      return;
    }
    setTasks((atual) =>
      atual.map((t) =>
        t.id === data.task_id
          ? {
              ...t,
              status: data.status,
              block_kind: data.block_kind,
              block_reason: data.block_reason,
            }
          : t,
      ),
    );
  };
  const aplicarEventoSseRef = useRef(aplicarEventoSse);
  useEffect(() => {
    aplicarEventoSseRef.current = aplicarEventoSse;
  });

  // Push via SSE reaproveitando o canal genérico de webhooks. Reconecta
  // manualmente em vez de confiar só no auto-retry do EventSource — dá
  // controle do delay e faz a reconexão ser determinística em teste. A
  // conexão não é por thread (SSE_URL é fixo) e o handler despacha sempre
  // via `aplicarEventoSseRef.current`, mantido atualizado acima — não
  // precisa recriar ao trocar de thread.
  useEffect(() => {
    let cancelado = false;
    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const conectar = () => {
      if (cancelado) return;
      es = new EventSource(SSE_URL);
      es.addEventListener("message", (ev: MessageEvent<string>) => {
        try {
          const parsed = JSON.parse(ev.data) as Partial<WebhookSseEvent>;
          if (parsed.provider !== "kanban" || !parsed.data?.task_id) return;
          aplicarEventoSseRef.current(parsed.data);
        } catch {
          // Evento malformado não pode derrubar a conexão — o polling de
          // reconciliação cobre o que se perder aqui.
        }
      });
      es.addEventListener("error", () => {
        es?.close();
        if (!cancelado)
          reconnectTimer = setTimeout(conectar, SSE_RECONNECT_DELAY_MS);
      });
    };
    conectar();

    return () => {
      cancelado = true;
      es?.close();
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };
  }, []);

  // `archived` só entra quando o filtro "mostrar arquivadas" está ativo.
  const colunasAtivas = showArchived
    ? [
        ...COLUNAS,
        {
          status: "archived",
          label: () => m.kanban_column_archived(),
          tone: "archived",
        },
      ]
    : COLUNAS;

  const tenants = Array.from(
    new Set(tasks.map((t) => t.workspace_id).filter((v): v is string => !!v)),
  ).toSorted();
  const assignees = Array.from(
    new Set(
      tasks.map((t) => t.agent_profile_id).filter((v): v is string => !!v),
    ),
  ).toSorted();

  const buscaLower = search.trim().toLowerCase();
  const visiveis = tasks.filter((t) => {
    // `archived` é o único status que pode ser escondido, e só por filtro.
    // Qualquer outro status desconhecido cai na lane de fallback — nunca
    // some do board.
    if (!showArchived && t.status === "archived") return false;
    if (buscaLower && !t.name.toLowerCase().includes(buscaLower)) return false;
    if (tenantFilter && t.workspace_id !== tenantFilter) return false;
    if (assigneeFilter && t.agent_profile_id !== assigneeFilter) return false;
    return true;
  });

  const toggleSelect = (taskId: string, shiftKey: boolean) => {
    setSelected((atual) => {
      const next = new Set(atual);
      if (shiftKey && lastSelectedId) {
        // Range simples na ordem em que os cards visíveis aparecem — não
        // precisa respeitar coluna, só a ordem visual do board.
        const ids = visiveis.map((t) => t.id);
        const de = ids.indexOf(lastSelectedId);
        const ate = ids.indexOf(taskId);
        if (de !== -1 && ate !== -1) {
          const [ini, fim] = de < ate ? [de, ate] : [ate, de];
          for (const id of ids.slice(ini, fim + 1)) next.add(id);
        } else {
          next.add(taskId);
        }
      } else if (next.has(taskId)) {
        next.delete(taskId);
      } else {
        next.add(taskId);
      }
      return next;
    });
    setLastSelectedId(taskId);
  };

  const arquivarSelecionadas = () => {
    const ids = Array.from(selected);
    void bulkArchive(threadId, ids).then(() => {
      setSelected(new Set());
      carregar();
    });
  };

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over) return;
    const task = tasks.find((t) => t.id === active.id);
    if (!task) return;
    const targetStatus = String(over.id);
    void applyDragTransition(threadId, task, targetStatus).then((aplicado) => {
      if (aplicado) carregar();
    });
  };

  // Lanes vazias viram trilho quando o board tem trabalho em alguma outra —
  // sem isso, 8 colunas de 240px forçam scroll horizontal em qualquer janela.
  const boardTemTrabalho = visiveis.length > 0;

  const temOrfas = visiveis.some(
    (t) => !colunasAtivas.some((c) => c.status === t.status),
  );
  const lanes = temOrfas
    ? [
        ...colunasAtivas,
        {
          status: FALLBACK_STATUS,
          label: () => m.kanban_column_other(),
          tone: "archived",
        },
      ]
    : colunasAtivas;

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden p-4">
      <div className="shrink-0">
        <BoardSwitcher
          boards={boards}
          activeBoardId={activeBoardId}
          onChange={trocarBoard}
          onCreated={(board) => {
            setBoards((prev) => [...prev, board]);
            trocarBoard(board.id);
          }}
        />
        <NewTaskForm
          threadId={threadId}
          boardId={activeBoardId}
          onCreated={carregar}
        />
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={m.kanban_filter_search_placeholder()}
            aria-label={m.kanban_filter_search_label()}
            className="w-40 rounded border bg-background px-2 py-1 text-xs"
          />
          {tenants.length > 0 && (
            <select
              value={tenantFilter}
              onChange={(e) => setTenantFilter(e.target.value)}
              aria-label={m.kanban_filter_tenant_all()}
              className="rounded border bg-background px-2 py-1 text-xs"
            >
              <option value="">{m.kanban_filter_tenant_all()}</option>
              {tenants.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          )}
          {assignees.length > 0 && (
            <select
              value={assigneeFilter}
              onChange={(e) => setAssigneeFilter(e.target.value)}
              aria-label={m.kanban_filter_assignee_all()}
              className="rounded border bg-background px-2 py-1 text-xs"
            >
              <option value="">{m.kanban_filter_assignee_all()}</option>
              {assignees.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          )}
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
            />
            {m.kanban_filter_show_archived()}
          </label>
        </div>
        {selected.size > 0 && (
          <div className="mb-3 flex items-center gap-3 rounded-md border bg-card px-3 py-1.5">
            <span className="text-xs">
              {m.kanban_selection_count({ n: selected.size })}
            </span>
            <button
              onClick={arquivarSelecionadas}
              className="text-xs text-primary hover:underline"
            >
              {m.kanban_action_archive()}
            </button>
          </div>
        )}
        {tasks.length === 0 && (
          <p className="mb-2 text-xs text-muted-foreground">
            {m.kanban_empty()}
          </p>
        )}
      </div>
      <DndContext onDragEnd={handleDragEnd}>
        <div className="shrink-0">
          <TriageDropzone />
        </div>
        {/* Lanes quebram em grid (flex-wrap com flex-basis heterogêneo —
            colunas abertas crescem, trilhos colapsados ficam fixos em
            2.25rem), nunca scroll horizontal. Só o trilho de lanes rola
            verticalmente quando não cabem todas na altura disponível; cada
            lane aberta também rola por dentro (ver Column). */}
        <div className="app-scrollbar flex min-h-0 flex-1 flex-wrap content-start gap-3 overflow-y-auto lg:max-h-[38rem]">
          {lanes.map((coluna) => {
            const daColuna = visiveis.filter((t) =>
              coluna.status === FALLBACK_STATUS
                ? !colunasAtivas.some((c) => c.status === t.status)
                : t.status === coluna.status,
            );
            const autoColapsada = boardTemTrabalho && daColuna.length === 0;
            return (
              <Column
                key={coluna.status}
                status={coluna.status}
                label={coluna.label()}
                count={daColuna.length}
                collapsed={
                  // Self-heal: um override manual só vale enquanto a lane
                  // segue vazia. Task nova (ex.: via SSE) descarta o
                  // colapso manual — senão ela fica escondida sem o
                  // usuário perceber.
                  daColuna.length > 0
                    ? autoColapsada
                    : (laneOverrides[coluna.status] ?? autoColapsada)
                }
                onToggle={() =>
                  setLaneOverrides((prev) => ({
                    ...prev,
                    [coluna.status]: !(prev[coluna.status] ?? autoColapsada),
                  }))
                }
              >
                {daColuna.map((task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    threadId={threadId}
                    selected={selected.has(task.id)}
                    onToggleSelect={(shiftKey) =>
                      toggleSelect(task.id, shiftKey)
                    }
                    onChanged={carregar}
                  />
                ))}
              </Column>
            );
          })}
        </div>
      </DndContext>
    </div>
  );
}

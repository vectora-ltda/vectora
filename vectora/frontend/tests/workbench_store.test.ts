/**
 * workbench-store
 *
 * Cobre:
 * - Shell (terminais, painel, aba ativa, split, pins) — persistido.
 * - Slices voláteis Files/Git/Plan — sobrevivem a remount, não a reload.
 * - Invalidate por SSE (zera fetchedAt sem apagar conteúdo).
 * - Referências estáveis (EMPTY_*) — não causam infinite loop.
 *
 * O teste reseta o store entre casos via `setState({...defaults})` para
 * isolar (Zustand é singleton por módulo).
 */

import { describe, it, expect, beforeEach } from "vitest";
import {
  useWorkbenchStore,
  WORKBENCH_TABS,
  type TerminalInstance,
} from "../lib/stores/workbench-store";

function reset() {
  useWorkbenchStore.setState({
    byThread: {},
    activeByThread: {},
    panelOpen: {},
    activeTabByThread: {},
    splitSize: 224,
    pinnedFiles: {},
    files: {},
    git: {},
    plan: {},
    tasks: {},
  });
}

beforeEach(reset);

// ---------------------------------------------------------------------------
// WORKBENCH_TABS — constante exportada
// ---------------------------------------------------------------------------

describe("WORKBENCH_TABS", () => {
  it("expõe as abas na ordem da UI", () => {
    expect(WORKBENCH_TABS).toEqual([
      "files",
      "git",
      "plan",
      "tasks",
      "browser",
      "storage",
      "context_graph",
      "library",
      "terminal",
    ]);
  });
});

// ---------------------------------------------------------------------------
// Shell — terminais
// ---------------------------------------------------------------------------

describe("workbench-store: terminais (shell)", () => {
  const inst: TerminalInstance = {
    id: "t1",
    title: "shell",
    workspaceId: "ws-a",
  };

  it("list() devolve EMPTY_LIST estável quando thread não tem terminais", () => {
    const s = useWorkbenchStore.getState();
    const a = s.list("thread-X");
    const b = s.list("thread-X");
    expect(a).toEqual([]);
    // Mesma referência → não causa re-render em useSyncExternalStore.
    expect(a).toBe(b);
  });

  it("open() adiciona terminal e marca panel aberto + aba terminal", () => {
    useWorkbenchStore.getState().open("thread-1", inst);
    const s = useWorkbenchStore.getState();
    expect(s.list("thread-1")).toEqual([inst]);
    expect(s.isOpen("thread-1")).toBe(true);
    expect(s.getActiveTab("thread-1")).toBe("terminal");
    expect(s.active("thread-1")).toEqual(inst);
  });

  it("open() do mesmo id é idempotente — não duplica", () => {
    useWorkbenchStore.getState().open("thread-1", inst);
    useWorkbenchStore.getState().open("thread-1", inst);
    expect(useWorkbenchStore.getState().list("thread-1")).toHaveLength(1);
  });

  it("close() remove e re-aponta active para o próximo terminal", () => {
    const a = { id: "a", title: "a", workspaceId: "ws" };
    const b = { id: "b", title: "b", workspaceId: "ws" };
    useWorkbenchStore.getState().open("thread-1", a);
    useWorkbenchStore.getState().open("thread-1", b);
    useWorkbenchStore.getState().setActive("thread-1", "b");

    useWorkbenchStore.getState().close("thread-1", "b");
    const s = useWorkbenchStore.getState();
    expect(s.list("thread-1")).toEqual([a]);
    expect(s.active("thread-1")?.id).toBe("a");
  });

  it("togglePanel() inverte o estado por thread", () => {
    const t = useWorkbenchStore.getState().togglePanel;
    t("th");
    expect(useWorkbenchStore.getState().isOpen("th")).toBe(true);
    t("th");
    expect(useWorkbenchStore.getState().isOpen("th")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Shell — aba ativa, split, pins
// ---------------------------------------------------------------------------

describe("workbench-store: shell extra", () => {
  it("setActiveTab() troca a aba e abre o painel", () => {
    useWorkbenchStore.getState().setActiveTab("th", "files");
    const s = useWorkbenchStore.getState();
    expect(s.getActiveTab("th")).toBe("files");
    expect(s.isOpen("th")).toBe(true);
  });

  it("getActiveTab() default é 'files' sem plano ativo", () => {
    expect(useWorkbenchStore.getState().getActiveTab("never-seen")).toBe(
      "files",
    );
  });

  it("getActiveTab() default é 'plan' quando há itens de plano", () => {
    useWorkbenchStore.getState().setPlanItems("th-plan", [
      {
        title: "Plano X",
        path: "plans/x.md",
        session_id: "th-plan",
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    expect(useWorkbenchStore.getState().getActiveTab("th-plan")).toBe("plan");
  });

  it("setSplitSize() persiste tamanho do painel", () => {
    useWorkbenchStore.getState().setSplitSize(55);
    expect(useWorkbenchStore.getState().splitSize).toBe(55);
  });

  it("togglePinned() alterna pin de arquivo", () => {
    const { togglePinned, isPinned } = useWorkbenchStore.getState();
    togglePinned("th", "src/main.ts");
    expect(isPinned("th", "src/main.ts")).toBe(true);
    togglePinned("th", "src/main.ts");
    expect(isPinned("th", "src/main.ts")).toBe(false);
  });

  it("pins são isolados por thread", () => {
    const { togglePinned, isPinned } = useWorkbenchStore.getState();
    togglePinned("a", "x.md");
    expect(isPinned("a", "x.md")).toBe(true);
    expect(isPinned("b", "x.md")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Files slice
// ---------------------------------------------------------------------------

describe("workbench-store: files slice", () => {
  it("getFiles() devolve cache vazio estável para workspace novo", () => {
    const a = useWorkbenchStore.getState().getFiles("ws-X");
    const b = useWorkbenchStore.getState().getFiles("ws-X");
    expect(a.expandedDirs).toEqual([]);
    expect(a.entriesByDir).toEqual({});
    expect(a.openPath).toBeNull();
    // Mesma referência — não dispara re-render.
    expect(a).toBe(b);
  });

  it("setFilesEntries() popula entriesByDir e marca fetchedAt", () => {
    const before = Date.now();
    useWorkbenchStore
      .getState()
      .setFilesEntries("ws", "src", [
        { name: "main.ts", path: "src/main.ts", kind: "file" },
      ]);
    const cache = useWorkbenchStore.getState().getFiles("ws");
    expect(cache.entriesByDir["src"]).toHaveLength(1);
    expect(cache.fetchedAt["src"]).toBeGreaterThanOrEqual(before);
  });

  it("toggleExpanded() alterna pasta na lista", () => {
    const { toggleExpanded } = useWorkbenchStore.getState();
    toggleExpanded("ws", "src");
    expect(useWorkbenchStore.getState().getFiles("ws").expandedDirs).toContain(
      "src",
    );
    toggleExpanded("ws", "src");
    expect(
      useWorkbenchStore.getState().getFiles("ws").expandedDirs,
    ).not.toContain("src");
  });

  it("setOpenFile() / setFileContent() — viewer e conteúdo", () => {
    useWorkbenchStore.getState().setOpenFile("ws", "README.md");
    useWorkbenchStore.getState().setFileContent("ws", "README.md", {
      path: "README.md",
      kind: "text",
      content: "# Hi",
      size: 4,
    });
    const cache = useWorkbenchStore.getState().getFiles("ws");
    expect(cache.openPath).toBe("README.md");
    expect(cache.contents["README.md"]?.content).toBe("# Hi");
  });

  it("LRU: mantém apenas os últimos 8 conteúdos", () => {
    const { setFileContent } = useWorkbenchStore.getState();
    for (let i = 0; i < 12; i++) {
      setFileContent("ws", `file${i}.md`, {
        path: `file${i}.md`,
        kind: "text",
        content: String(i),
        size: 1,
      });
    }
    const cache = useWorkbenchStore.getState().getFiles("ws");
    const keys = Object.keys(cache.contents);
    expect(keys).toHaveLength(8);
    // Os 8 últimos (file4..file11) ficaram
    expect(keys).toContain("file11.md");
    expect(keys).toContain("file4.md");
    expect(keys).not.toContain("file0.md");
  });

  it("setFilesFilter() preserva entriesByDir (não invalida)", () => {
    useWorkbenchStore
      .getState()
      .setFilesEntries("ws", "src", [
        { name: "x.ts", path: "src/x.ts", kind: "file" },
      ]);
    useWorkbenchStore.getState().setFilesFilter("ws", "main");
    const cache = useWorkbenchStore.getState().getFiles("ws");
    expect(cache.filter).toBe("main");
    expect(cache.entriesByDir["src"]).toHaveLength(1);
  });

  it("invalidateFiles(wsId) zera fetchedAt mas mantém estrutura", () => {
    const { setFilesEntries, invalidateFiles } = useWorkbenchStore.getState();
    setFilesEntries("ws", "src", []);
    invalidateFiles("ws");
    const cache = useWorkbenchStore.getState().getFiles("ws");
    // Estrutura ainda existe (caller verá entries vazias mas isStale=true).
    expect(cache.fetchedAt).toEqual({});
  });

  it("invalidateFiles() sem arg zera todos os workspaces", () => {
    const { setFilesEntries, invalidateFiles } = useWorkbenchStore.getState();
    setFilesEntries("ws-a", "", []);
    setFilesEntries("ws-b", "", []);
    invalidateFiles();
    expect(useWorkbenchStore.getState().files).toEqual({});
  });
});

// ---------------------------------------------------------------------------
// Diff slice
// ---------------------------------------------------------------------------

describe("workbench-store: Git workbench cache", () => {
  it("getGit() devolve cache vazio estável", () => {
    const a = useWorkbenchStore.getState().getGit("ws");
    const b = useWorkbenchStore.getState().getGit("ws");
    expect(a.summary).toBeNull();
    expect(a).toBe(b);
  });

  it("setGitSummary() popula resumo + timestamp", () => {
    const before = Date.now();
    useWorkbenchStore.getState().setGitSummary("ws", {
      is_git_repo: true,
      total_additions: 5,
      total_deletions: 2,
      files: [],
    });
    const cache = useWorkbenchStore.getState().getGit("ws");
    expect(cache.summary?.total_additions).toBe(5);
    expect(cache.summaryFetchedAt).toBeGreaterThanOrEqual(before);
  });

  it("setGitOpenFile() add/remove arquivo aberto sem duplicar", () => {
    const { setGitOpenFile } = useWorkbenchStore.getState();
    setGitOpenFile("ws", "x.md", true);
    setGitOpenFile("ws", "x.md", true); // idempotente
    expect(useWorkbenchStore.getState().getGit("ws").openFiles).toEqual([
      "x.md",
    ]);
    setGitOpenFile("ws", "x.md", false);
    expect(useWorkbenchStore.getState().getGit("ws").openFiles).toEqual([]);
  });

  it("setGitHunks() armazena hunks por path", () => {
    useWorkbenchStore
      .getState()
      .setGitHunks("ws", "x.md", [{ header: "@@", lines: ["+a"] }]);
    expect(
      useWorkbenchStore.getState().getGit("ws").hunksByFile["x.md"],
    ).toHaveLength(1);
  });

  it("invalidateGit(wsId) zera timestamps sem apagar summary", () => {
    const { setGitSummary, invalidateGit } = useWorkbenchStore.getState();
    setGitSummary("ws", {
      is_git_repo: true,
      total_additions: 0,
      total_deletions: 0,
      files: [],
    });
    invalidateGit("ws");
    const cache = useWorkbenchStore.getState().getGit("ws");
    expect(cache.summaryFetchedAt).toBe(0);
    expect(cache.fileFetchedAt).toEqual({});
  });
});

// ---------------------------------------------------------------------------
// Plan slice
// ---------------------------------------------------------------------------

describe("workbench-store: plan slice", () => {
  it("getPlan() devolve cache vazio estável", () => {
    const a = useWorkbenchStore.getState().getPlan("th");
    const b = useWorkbenchStore.getState().getPlan("th");
    expect(a.items).toEqual([]);
    expect(a.openSlugs).toEqual([]);
    expect(a).toBe(b);
  });

  it("setPlanItems() popula lista e timestamp", () => {
    const before = Date.now();
    useWorkbenchStore.getState().setPlanItems("th", [
      {
        title: "Plano A",
        path: "/a.md",
        session_id: "th",
        created_at: "2025",
      },
    ]);
    const cache = useWorkbenchStore.getState().getPlan("th");
    expect(cache.items).toHaveLength(1);
    expect(cache.fetchedAt).toBeGreaterThanOrEqual(before);
  });

  it("togglePlanOpenSlug() / setPlanContent() — viewer e conteúdo", () => {
    useWorkbenchStore.getState().togglePlanOpenSlug("th", "plano-a");
    useWorkbenchStore.getState().setPlanContent("th", "plano-a", "# Plano A");
    const cache = useWorkbenchStore.getState().getPlan("th");
    expect(cache.openSlugs).toEqual(["plano-a"]);
    expect(cache.contentsBySlug["plano-a"]).toBe("# Plano A");
  });

  it("togglePlanOpenSlug() abre múltiplos slugs simultaneamente (Accordion multiple)", () => {
    const { togglePlanOpenSlug } = useWorkbenchStore.getState();
    togglePlanOpenSlug("th-multi", "plano-a");
    togglePlanOpenSlug("th-multi", "plano-b");
    expect(useWorkbenchStore.getState().getPlan("th-multi").openSlugs).toEqual([
      "plano-a",
      "plano-b",
    ]);
  });

  it("togglePlanOpenSlug() fecha só o slug clicado, sem afetar os demais abertos", () => {
    const { togglePlanOpenSlug } = useWorkbenchStore.getState();
    togglePlanOpenSlug("th-close", "plano-a");
    togglePlanOpenSlug("th-close", "plano-b");
    togglePlanOpenSlug("th-close", "plano-a");
    expect(useWorkbenchStore.getState().getPlan("th-close").openSlugs).toEqual([
      "plano-b",
    ]);
  });

  it("erro/borda: toggle duplo do mesmo slug é idempotente (abre, fecha, volta ao estado inicial)", () => {
    const { togglePlanOpenSlug } = useWorkbenchStore.getState();
    togglePlanOpenSlug("th-idem", "plano-a");
    togglePlanOpenSlug("th-idem", "plano-a");
    expect(useWorkbenchStore.getState().getPlan("th-idem").openSlugs).toEqual(
      [],
    );
  });

  it("invalidatePlan(threadId) zera fetchedAt", () => {
    const { setPlanItems, invalidatePlan } = useWorkbenchStore.getState();
    setPlanItems("th", []);
    invalidatePlan("th");
    expect(useWorkbenchStore.getState().getPlan("th").fetchedAt).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Tasks slice
// ---------------------------------------------------------------------------

describe("workbench-store: tasks slice", () => {
  it("getTasks() devolve cache vazio estável", () => {
    const a = useWorkbenchStore.getState().getTasks("th");
    const b = useWorkbenchStore.getState().getTasks("th");
    expect(a.tasks).toEqual([]);
    expect(a.runs).toEqual([]);
    expect(a.fetchedAt).toBe(0);
    expect(a).toBe(b);
  });

  it("setTasksData() popula tasks/runs e marca fetchedAt", () => {
    const before = Date.now();
    useWorkbenchStore.getState().setTasksData(
      "th",
      [
        {
          id: "t1",
          session_id: "th",
          workspace_id: null,
          kind: "routine",
          name: "n",
          instruction: "i",
          trigger_type: "manual",
          trigger_config: {},
          enabled: true,
          last_run_at: null,
          next_run_at: null,
        },
      ],
      [],
    );
    const cache = useWorkbenchStore.getState().getTasks("th");
    expect(cache.tasks).toHaveLength(1);
    expect(cache.fetchedAt).toBeGreaterThanOrEqual(before);
  });

  it("invalidateTasks(threadId) zera fetchedAt sem apagar tasks/runs", () => {
    const { setTasksData, invalidateTasks } = useWorkbenchStore.getState();
    setTasksData("th", [], []);
    invalidateTasks("th");
    const cache = useWorkbenchStore.getState().getTasks("th");
    expect(cache.fetchedAt).toBe(0);
    expect(cache.tasks).toEqual([]);
  });

  it("erro/borda: invalidateTasks() sem threadId zera o slice inteiro", () => {
    const { setTasksData, invalidateTasks } = useWorkbenchStore.getState();
    setTasksData("a", [], []);
    setTasksData("b", [], []);
    invalidateTasks();
    expect(useWorkbenchStore.getState().tasks).toEqual({});
  });
});

// ---------------------------------------------------------------------------
// Todos (write_todos / TodoListMiddleware — Plan Mode real)
// ---------------------------------------------------------------------------

describe("workbench-store: todos slice", () => {
  it("getTodos() devolve lista vazia estável quando thread não tem todos", () => {
    const a = useWorkbenchStore.getState().getTodos("th-todos");
    const b = useWorkbenchStore.getState().getTodos("th-todos");
    expect(a).toEqual([]);
    expect(a).toBe(b);
  });

  it("setTodos() substitui a lista inteira (write_todos não é incremental)", () => {
    useWorkbenchStore.getState().setTodos("th-todos", [
      { content: "passo 1", status: "pending" },
      { content: "passo 2", status: "pending" },
    ]);
    useWorkbenchStore
      .getState()
      .setTodos("th-todos", [{ content: "passo 1", status: "completed" }]);
    const todos = useWorkbenchStore.getState().getTodos("th-todos");
    expect(todos).toEqual([{ content: "passo 1", status: "completed" }]);
  });

  it("setTodos() é isolado por thread", () => {
    useWorkbenchStore
      .getState()
      .setTodos("th-a", [{ content: "a", status: "pending" }]);
    useWorkbenchStore
      .getState()
      .setTodos("th-b", [{ content: "b", status: "in_progress" }]);
    expect(useWorkbenchStore.getState().getTodos("th-a")).toEqual([
      { content: "a", status: "pending" },
    ]);
    expect(useWorkbenchStore.getState().getTodos("th-b")).toEqual([
      { content: "b", status: "in_progress" },
    ]);
  });
});

// ---------------------------------------------------------------------------
// Pendência de atualização por aba
// ---------------------------------------------------------------------------

describe("workbench-store: pending", () => {
  it("markPending(wsId) marca files e diff como pendentes", () => {
    useWorkbenchStore.getState().markPending("ws");
    expect(useWorkbenchStore.getState().pending["ws"]).toEqual({
      files: true,
      git: true,
    });
  });

  it("clearPending(wsId, key) limpa só a categoria informada", () => {
    useWorkbenchStore.getState().markPending("ws");
    useWorkbenchStore.getState().clearPending("ws", "git");
    expect(useWorkbenchStore.getState().pending["ws"]).toEqual({
      files: true,
      git: false,
    });
  });

  it("clearPending sem pendência prévia é no-op", () => {
    useWorkbenchStore.getState().clearPending("ws-vazio", "files");
    expect(useWorkbenchStore.getState().pending["ws-vazio"]).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Isolamento por thread / workspace
// ---------------------------------------------------------------------------

describe("workbench-store: isolamento", () => {
  it("dois workspaces têm caches files independentes", () => {
    useWorkbenchStore
      .getState()
      .setFilesEntries("ws-a", "", [
        { name: "a.md", path: "a.md", kind: "file" },
      ]);
    expect(useWorkbenchStore.getState().getFiles("ws-b").entriesByDir).toEqual(
      {},
    );
  });

  it("duas threads têm planos independentes", () => {
    useWorkbenchStore.getState().setPlanItems("th-1", [
      {
        title: "P1",
        path: "/p1.md",
        session_id: "th-1",
        created_at: "2025",
      },
    ]);
    expect(useWorkbenchStore.getState().getPlan("th-2").items).toEqual([]);
  });
});

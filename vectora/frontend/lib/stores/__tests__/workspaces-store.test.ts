/**
 * Tests do `workspaces-store`: leitura pura + ações async. Cada caminho feliz
 * tem o par de erro/borda — payload malformado do backend, HTTP !ok, queda de
 * rede — que DEVE virar status de erro + toast (canal único de feedback), nunca
 * sucesso silencioso (CLAUDE.md §18).
 */

import { describe, expect, it, beforeEach, vi, type Mock } from "vitest";
import { useWorkspacesStore, type WorkspaceInfo } from "../workspaces-store";
import { useToastStore } from "../toast-store";
import { fetchJsonWithRetry } from "@/lib/utils/fetch-retry";
import {
  getBrowserSession,
  setBrowserSession,
} from "@/lib/browser-session-store";

vi.mock("@/lib/utils/fetch-retry", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/utils/fetch-retry")>();
  return { ...actual, fetchJsonWithRetry: vi.fn() };
});

const retryMock = fetchJsonWithRetry as unknown as Mock;

// `error` da toast store é injetado como mock ESTÁVEL (via setState) e resetado
// a cada teste. `vi.spyOn` vazaria: cada toast real faz setState e troca o
// objeto de estado, então o spy do teste anterior continuaria contando.
type ToastError = ReturnType<typeof useToastStore.getState>["error"];
const toastError = vi.fn();

function ws(id: string, over: Partial<WorkspaceInfo> = {}): WorkspaceInfo {
  return {
    id,
    name: id,
    cwd: `/home/${id}`,
    trusted: false,
    is_git_repo: false,
    git_remote: null,
    git_current_branch: null,
    git_default_branch: null,
    ...over,
  } as WorkspaceInfo;
}

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as unknown as Response;
}

beforeEach(() => {
  vi.unstubAllGlobals();
  retryMock.mockReset();
  toastError.mockReset();
  useToastStore.setState({ error: toastError as unknown as ToastError });
  useWorkspacesStore.setState({
    workspaces: [],
    active_id: null,
    fetchedAt: null,
    status: "idle",
    error: null,
    pending: { hydrate: false, create: false, trust: false, gitInit: false },
    safeRoots: [],
  });
});

describe("workspaces-store — leitura pura", () => {
  it("getActive devolve null quando não há workspaces", () => {
    expect(useWorkspacesStore.getState().getActive()).toBeNull();
  });

  it("getActive cai no primeiro workspace quando active_id é nulo", () => {
    useWorkspacesStore.getState().setWorkspaces([ws("a"), ws("b")], null);
    expect(useWorkspacesStore.getState().getActive()?.id).toBe("a");
  });

  it("getActive devolve o workspace ativo quando active_id casa", () => {
    useWorkspacesStore.getState().setWorkspaces([ws("a"), ws("b")], "b");
    expect(useWorkspacesStore.getState().getActive()?.id).toBe("b");
  });

  it("getActive cai no primeiro quando active_id não existe na lista", () => {
    useWorkspacesStore.getState().setWorkspaces([ws("a"), ws("b")], "fantasma");
    expect(useWorkspacesStore.getState().getActive()?.id).toBe("a");
  });

  it("getById encontra por id ou devolve null", () => {
    useWorkspacesStore.getState().setWorkspaces([ws("a"), ws("b")], "a");
    expect(useWorkspacesStore.getState().getById("b")?.id).toBe("b");
    expect(useWorkspacesStore.getState().getById("z")).toBeNull();
  });

  it("setWorkspaces marca fetchedAt e invalidate o zera", () => {
    useWorkspacesStore.getState().setWorkspaces([ws("a")], "a");
    expect(useWorkspacesStore.getState().fetchedAt).toBeGreaterThan(0);
    useWorkspacesStore.getState().invalidate();
    expect(useWorkspacesStore.getState().fetchedAt).toBeNull();
  });
});

describe("workspaces-store — transições de conta e concorrência", () => {
  it("aceita o active_id do servidor quando a seleção local ficou inválida", async () => {
    useWorkspacesStore.setState({ active_id: "revogado" });
    retryMock.mockResolvedValueOnce({
      workspaces: [ws("servidor")],
      active_id: "servidor",
    });
    await useWorkspacesStore.getState().hydrate();
    expect(useWorkspacesStore.getState().active_id).toBe("servidor");
  });

  it("reverte uma seleção otimista rejeitada pelo servidor", async () => {
    useWorkspacesStore.setState({ active_id: "anterior" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ message: "negado" }, false, 403)),
    );
    await useWorkspacesStore.getState().setActive("recusado");
    expect(useWorkspacesStore.getState().active_id).toBe("anterior");
  });

  it("reverte uma resposta HTTP 200 com status de erro", async () => {
    useWorkspacesStore.setState({ active_id: "anterior" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ status: "error" })),
    );
    await useWorkspacesStore.getState().setActive("inexistente");
    expect(useWorkspacesStore.getState().active_id).toBe("anterior");
  });

  it("reverte uma seleção vazia rejeitada pelo servidor", async () => {
    useWorkspacesStore.setState({ active_id: "anterior" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ status: "error" }, false, 400)),
    );
    await useWorkspacesStore.getState().setActive("");
    expect(useWorkspacesStore.getState().active_id).toBe("anterior");
  });

  it("reconcilia uma falha de rede com o workspace persistido no servidor", async () => {
    useWorkspacesStore.setState({ active_id: "anterior" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("offline");
      }),
    );
    retryMock.mockResolvedValueOnce({
      workspaces: [ws("servidor")],
      active_id: "servidor",
    });
    await useWorkspacesStore.getState().setActive("servidor");
    expect(useWorkspacesStore.getState().active_id).toBe("servidor");
  });

  it("preserva o workspace selecionado quando a hidratação termina depois", async () => {
    let resolveHydrate: ((value: unknown) => void) | undefined;
    retryMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveHydrate = resolve;
      }),
    );
    const hydration = useWorkspacesStore.getState().hydrate();
    await Promise.resolve();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ status: "ok" })),
    );
    await useWorkspacesStore.getState().setActive("local");
    resolveHydrate?.({ workspaces: [ws("local")], active_id: "servidor" });
    await hydration;
    expect(useWorkspacesStore.getState().workspaces).toHaveLength(1);
    expect(useWorkspacesStore.getState().active_id).toBe("local");
  });

  it("preserva a última seleção na corrida A-B-A", async () => {
    let resolveHydrate: ((value: unknown) => void) | undefined;
    useWorkspacesStore.setState({ active_id: "a" });
    retryMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveHydrate = resolve;
      }),
    );
    const hydration = useWorkspacesStore.getState().hydrate();
    await Promise.resolve();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ status: "ok" })),
    );
    await useWorkspacesStore.getState().setActive("b");
    await useWorkspacesStore.getState().setActive("a");
    resolveHydrate?.({ workspaces: [ws("a"), ws("b")], active_id: "b" });
    await hydration;
    expect(useWorkspacesStore.getState().active_id).toBe("a");
  });

  it("descarta sessões de navegador ao trocar de conta", () => {
    useWorkspacesStore.getState().setWorkspaces([ws("antiga")], "antiga");
    setBrowserSession("antiga:thread", {
      tabs: [],
      activeTabId: "",
    });
    useWorkspacesStore.getState().resetForUser();
    expect(getBrowserSession("antiga:thread")).toBeUndefined();
  });
});

describe("workspaces-store — hydrate", () => {
  it("popula workspaces/active_id e marca sucesso", async () => {
    retryMock.mockResolvedValueOnce({
      workspaces: [ws("a"), ws("b")],
      active_id: "b",
    });
    await useWorkspacesStore.getState().hydrate();
    const s = useWorkspacesStore.getState();
    expect(s.workspaces.map((w) => w.id)).toEqual(["a", "b"]);
    expect(s.active_id).toBe("b");
    expect(s.status).toBe("success");
    expect(s.pending.hydrate).toBe(false);
  });

  // ── par de erro: payload malformado NÃO pode virar sucesso ──
  it("payload sem `workspaces` → status erro + toast", async () => {
    retryMock.mockResolvedValueOnce({ active_id: "x" });
    await useWorkspacesStore.getState().hydrate();
    const s = useWorkspacesStore.getState();
    expect(s.status).toBe("error");
    expect(s.error).toBeTruthy();
    expect(s.pending.hydrate).toBe(false);
    expect(toastError).toHaveBeenCalledOnce();
  });

  it("queda de rede → status erro + toast", async () => {
    retryMock.mockRejectedValueOnce(new Error("offline"));
    await useWorkspacesStore.getState().hydrate();
    expect(useWorkspacesStore.getState().status).toBe("error");
    expect(toastError).toHaveBeenCalledOnce();
  });
});

describe("workspaces-store — create", () => {
  it("sucesso: hidrata, fixa o active e devolve ok", async () => {
    const created = ws("new");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ status: "ok", workspace: created })),
    );
    retryMock.mockResolvedValueOnce({
      workspaces: [created],
      active_id: "new",
    });
    const res = await useWorkspacesStore
      .getState()
      .create("/x", { trust: true });
    expect(res.ok).toBe(true);
    expect(res.ok && res.data.id).toBe("new");
    expect(useWorkspacesStore.getState().active_id).toBe("new");
    expect(useWorkspacesStore.getState().pending.create).toBe(false);
  });

  it("HTTP !ok → ok:false com a mensagem do backend + toast", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ message: "negado" }, false, 403)),
    );
    const res = await useWorkspacesStore.getState().create("/x");
    expect(res.ok).toBe(false);
    expect(!res.ok && res.error).toBe("negado");
    expect(toastError).toHaveBeenCalledOnce();
    expect(useWorkspacesStore.getState().pending.create).toBe(false);
  });

  it("status != ok no corpo → ok:false (resposta inesperada)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ status: "fail" })),
    );
    const res = await useWorkspacesStore.getState().create("/x");
    expect(res.ok).toBe(false);
  });
});

describe("workspaces-store — trust / gitInit", () => {
  it("trust substitui o workspace na lista", async () => {
    useWorkspacesStore.getState().setWorkspaces([ws("a"), ws("b")], "a");
    const trusted = ws("a", { trusted: true });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ status: "ok", workspace: trusted })),
    );
    const res = await useWorkspacesStore.getState().trust("a");
    expect(res.ok).toBe(true);
    expect(useWorkspacesStore.getState().getById("a")?.trusted).toBe(true);
  });

  it("gitInit com HTTP !ok → ok:false + toast, pending limpo", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ message: "sem git" }, false, 500)),
    );
    const res = await useWorkspacesStore.getState().gitInit("a");
    expect(res.ok).toBe(false);
    expect(toastError).toHaveBeenCalledOnce();
    expect(useWorkspacesStore.getState().pending.gitInit).toBe(false);
  });
});

describe("workspaces-store — leituras auxiliares (borda)", () => {
  it("browse devolve o resultado com path; null em payload sem path", async () => {
    retryMock.mockResolvedValueOnce({ path: "/x", parent: null, entries: [] });
    expect((await useWorkspacesStore.getState().browse("/x"))?.path).toBe("/x");
    retryMock.mockResolvedValueOnce({ erro: true });
    expect(await useWorkspacesStore.getState().browse("/y")).toBeNull();
  });

  it("listSshKeys: array vira lista; payload malformado vira []", async () => {
    retryMock.mockResolvedValueOnce({ keys: ["k1", "k2"] });
    expect(await useWorkspacesStore.getState().listSshKeys()).toEqual([
      "k1",
      "k2",
    ]);
    retryMock.mockResolvedValueOnce({ keys: "nope" });
    expect(await useWorkspacesStore.getState().listSshKeys()).toEqual([]);
  });

  it("loadSafeRoots só seta safeRoots quando roots é array", async () => {
    retryMock.mockResolvedValueOnce({ roots: "x" });
    await useWorkspacesStore.getState().loadSafeRoots();
    expect(useWorkspacesStore.getState().safeRoots).toEqual([]);
    retryMock.mockResolvedValueOnce({
      roots: [{ id: "r", path: "/", label: "root", builtin: true }],
    });
    await useWorkspacesStore.getState().loadSafeRoots();
    expect(useWorkspacesStore.getState().safeRoots).toHaveLength(1);
  });

  it("testSsh: queda de rede vira {ok:false} com a mensagem do erro", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("recusado");
      }),
    );
    const r = await useWorkspacesStore.getState().testSsh("host");
    expect(r.ok).toBe(false);
    expect(r.message).toContain("recusado");
  });
});

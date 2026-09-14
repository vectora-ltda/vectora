// @vitest-environment jsdom
/**
 * workbench-store — slice de pins. O backend é a fonte de verdade (§8):
 * togglePinned atualiza o cache otimisticamente e chama SetThreadPins; loadPins
 * lê GetThreadPins; setPins reconcilia. Mocka o vectora-client.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/api/vectora-client", () => ({
  getThreadPins: vi.fn(),
  setThreadPins: vi.fn(),
}));

import { useWorkbenchStore } from "@/lib/stores/workbench-store";
import { getThreadPins, setThreadPins } from "@/lib/api/vectora-client";

const mockGet = getThreadPins as unknown as ReturnType<typeof vi.fn>;
const mockSet = setThreadPins as unknown as ReturnType<typeof vi.fn>;

const s = () => useWorkbenchStore.getState();
const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  mockGet.mockReset();
  mockSet.mockReset();
  mockSet.mockResolvedValue({ thread_id: "t", pins: [] });
  useWorkbenchStore.setState({
    pinnedFiles: {},
    activeTabByThread: {},
    panelOpen: {},
  });
});

describe("Workbench — última aba ao reabrir", () => {
  it.each(["files", "diff", "plan", "browser", "terminal"] as const)(
    "preserva %s depois de fechar e reabrir",
    (tab) => {
      s().selectTab("t1", tab);
      s().setPanelOpen("t1", false);
      expect(s().isOpen("t1")).toBe(false);

      s().setPanelOpen("t1", true);
      expect(s().getActiveTab("t1")).toBe(tab);
      expect(s().isOpen("t1")).toBe(true);
    },
  );
});

describe("togglePinned — cache otimista", () => {
  it("adiciona um pin", () => {
    s().togglePinned("t1", "a.py");
    expect(s().pinnedFiles["t1"]).toEqual(["a.py"]);
    expect(s().isPinned("t1", "a.py")).toBe(true);
  });

  it("remove um pin já fixado", () => {
    s().togglePinned("t1", "a.py");
    s().togglePinned("t1", "a.py");
    expect(s().pinnedFiles["t1"]).toEqual([]);
    expect(s().isPinned("t1", "a.py")).toBe(false);
  });

  it("acumula múltiplos pins na ordem de fixação", () => {
    s().togglePinned("t1", "a.py");
    s().togglePinned("t1", "b.py");
    s().togglePinned("t1", "c.py");
    expect(s().pinnedFiles["t1"]).toEqual(["a.py", "b.py", "c.py"]);
  });

  it("remove só o alvo, preservando os demais", () => {
    s().togglePinned("t1", "a.py");
    s().togglePinned("t1", "b.py");
    s().togglePinned("t1", "a.py");
    expect(s().pinnedFiles["t1"]).toEqual(["b.py"]);
  });

  it("é otimista: o pin aparece antes do backend responder", () => {
    mockSet.mockReturnValue(new Promise(() => {})); // nunca resolve
    s().togglePinned("t1", "a.py");
    expect(s().isPinned("t1", "a.py")).toBe(true);
  });

  it("isola por thread", () => {
    s().togglePinned("t1", "a.py");
    expect(s().isPinned("t2", "a.py")).toBe(false);
    s().togglePinned("t2", "z.py");
    expect(s().pinnedFiles["t1"]).toEqual(["a.py"]);
    expect(s().pinnedFiles["t2"]).toEqual(["z.py"]);
  });
});

describe("togglePinned — sincronização com o backend", () => {
  it("chama setThreadPins com a lista nova ao adicionar", () => {
    s().togglePinned("t1", "a.py");
    expect(mockSet).toHaveBeenCalledWith("t1", ["a.py"]);
  });

  it("chama setThreadPins com a lista reduzida ao remover", () => {
    s().togglePinned("t1", "a.py");
    s().togglePinned("t1", "b.py");
    mockSet.mockClear();
    s().togglePinned("t1", "a.py");
    expect(mockSet).toHaveBeenCalledWith("t1", ["b.py"]);
  });

  it("reconcilia o cache com a lista normalizada do backend", async () => {
    mockSet.mockResolvedValueOnce({ thread_id: "t1", pins: ["a.py"] });
    s().togglePinned("t1", "a.py");
    await flush();
    expect(s().pinnedFiles["t1"]).toEqual(["a.py"]);
  });

  it("falha de rede não derruba a UI (cache otimista permanece)", async () => {
    mockSet.mockRejectedValueOnce(new Error("offline"));
    s().togglePinned("t1", "a.py");
    await flush();
    expect(s().isPinned("t1", "a.py")).toBe(true);
  });

  it("dispara uma chamada por toggle", () => {
    s().togglePinned("t1", "a.py");
    s().togglePinned("t1", "b.py");
    expect(mockSet).toHaveBeenCalledTimes(2);
  });
});

describe("setPins — reconciliação direta", () => {
  it("substitui o cache da thread", () => {
    s().togglePinned("t1", "a.py");
    s().setPins("t1", ["x.py", "y.py"]);
    expect(s().pinnedFiles["t1"]).toEqual(["x.py", "y.py"]);
  });

  it("aceita lista vazia (limpa)", () => {
    s().togglePinned("t1", "a.py");
    s().setPins("t1", []);
    expect(s().pinnedFiles["t1"]).toEqual([]);
  });

  it("não afeta outras threads", () => {
    s().setPins("t1", ["a.py"]);
    s().setPins("t2", ["b.py"]);
    expect(s().pinnedFiles["t1"]).toEqual(["a.py"]);
    expect(s().pinnedFiles["t2"]).toEqual(["b.py"]);
  });
});

describe("loadPins — carga do backend", () => {
  it("lê os pins via getThreadPins e popula o cache", async () => {
    mockGet.mockResolvedValueOnce({ thread_id: "t1", pins: ["a.py", "b.py"] });
    await s().loadPins("t1");
    expect(mockGet).toHaveBeenCalledWith("t1");
    expect(s().pinnedFiles["t1"]).toEqual(["a.py", "b.py"]);
  });

  it("sobrescreve o cache local com a verdade do backend", async () => {
    s().setPins("t1", ["antigo.py"]);
    mockGet.mockResolvedValueOnce({ thread_id: "t1", pins: ["novo.py"] });
    await s().loadPins("t1");
    expect(s().pinnedFiles["t1"]).toEqual(["novo.py"]);
  });

  it("backend vazio zera os pins", async () => {
    s().setPins("t1", ["a.py"]);
    mockGet.mockResolvedValueOnce({ thread_id: "t1", pins: [] });
    await s().loadPins("t1");
    expect(s().pinnedFiles["t1"]).toEqual([]);
  });

  it("erro mantém o cache atual e não lança", async () => {
    s().setPins("t1", ["a.py"]);
    mockGet.mockRejectedValueOnce(new Error("offline"));
    await expect(s().loadPins("t1")).resolves.toBeUndefined();
    expect(s().pinnedFiles["t1"]).toEqual(["a.py"]);
  });
});

describe("isPinned", () => {
  it("false para thread/path sem pin", () => {
    expect(s().isPinned("t1", "a.py")).toBe(false);
  });

  it("true após setPins", () => {
    s().setPins("t1", ["a.py"]);
    expect(s().isPinned("t1", "a.py")).toBe(true);
  });

  it("false para path não fixado na thread com outros pins", () => {
    s().setPins("t1", ["a.py"]);
    expect(s().isPinned("t1", "b.py")).toBe(false);
  });
});

describe("WORKBENCH_TABS — ordem das abas", () => {
  it("segue a ordem: files, git(diff), plan, background, browser, memory(storage), context_graph, library, terminal", async () => {
    const { WORKBENCH_TABS } = await import("@/lib/stores/workbench-store");
    expect(WORKBENCH_TABS).toEqual([
      "files",
      "diff",
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

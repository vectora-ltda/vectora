/**
 * Tests para o windows-store: janelas flutuantes com abas por workspace
 * (open/closeTab/setActiveTab/close/focus/minimize/restore/setBounds).
 */

import { describe, it, expect, beforeEach } from "vitest";
import { useWindowsStore } from "../windows-store";

const BASE_Z = 100;
const s = () => useWindowsStore.getState();
const winOf = (id: string) => s().windows.find((w) => w.id === id);

beforeEach(() => {
  useWindowsStore.setState({
    windows: [],
    topZ: BASE_Z,
    dockedWorkspaceId: null,
    dockedTabs: [],
    dockedActiveTab: null,
    canvasDocuments: [],
    activeCanvasDocumentId: null,
  });
  if (typeof localStorage !== "undefined") localStorage.clear();
});

describe("windows-store — open", () => {
  it("começa sem janelas", () => {
    expect(s().windows).toHaveLength(0);
  });

  it("open cria uma janela com id = workspaceId", () => {
    s().open("ws1", "src/a.ts");
    expect(winOf("ws1")).toBeDefined();
  });

  it("open define activeTab e title a partir do basename do path", () => {
    s().open("ws1", "src/deep/file.ts");
    const w = winOf("ws1")!;
    expect(w.activeTab).toBe("src/deep/file.ts");
    expect(w.title).toBe("file.ts");
  });

  it("open usa dimensões padrão 640x460", () => {
    s().open("ws1", "a.ts");
    const w = winOf("ws1")!;
    expect(w.w).toBe(640);
    expect(w.h).toBe(460);
  });

  it("open atribui zIndex acima do topo e incrementa topZ", () => {
    s().open("ws1", "a.ts");
    expect(s().topZ).toBe(BASE_Z + 1);
    expect(winOf("ws1")?.zIndex).toBe(BASE_Z + 1);
  });

  it("abrir segundo arquivo no mesmo workspace adiciona aba (não nova janela)", () => {
    s().open("ws1", "a.ts");
    s().open("ws1", "b.ts");
    expect(s().windows).toHaveLength(1);
    expect(winOf("ws1")?.tabs).toEqual(["a.ts", "b.ts"]);
  });

  it("abrir segundo arquivo ativa-o como activeTab", () => {
    s().open("ws1", "a.ts");
    s().open("ws1", "b.ts");
    expect(winOf("ws1")?.activeTab).toBe("b.ts");
    expect(winOf("ws1")?.title).toBe("b.ts");
  });

  it("open do mesmo path não duplica a aba — ativa e traz pro topo", () => {
    s().open("ws1", "a.ts");
    s().open("ws1", "b.ts");
    s().open("ws1", "a.ts");
    expect(winOf("ws1")?.tabs).toEqual(["a.ts", "b.ts"]);
    expect(winOf("ws1")?.activeTab).toBe("a.ts");
  });

  it("open de workspace diferente cria janela separada", () => {
    s().open("ws1", "a.ts");
    s().open("ws2", "a.ts");
    expect(s().windows).toHaveLength(2);
    expect(winOf("ws1")).toBeDefined();
    expect(winOf("ws2")).toBeDefined();
  });

  it("open restaura janela minimizada ao abrir aba existente", () => {
    s().open("ws1", "a.ts");
    s().minimize("ws1");
    s().open("ws1", "a.ts");
    expect(winOf("ws1")?.minimized).toBe(false);
  });

  it("janelas novas cascateiam x/y", () => {
    s().open("ws1", "a.ts");
    s().open("ws2", "b.ts");
    expect(winOf("ws1")?.x).not.toBe(winOf("ws2")?.x);
  });

  it("path sem barra usa o próprio path como título", () => {
    s().open("ws1", "README");
    expect(winOf("ws1")?.title).toBe("README");
  });
});

describe("windows-store — close", () => {
  it("close remove a janela inteira", () => {
    s().open("ws1", "a.ts");
    s().close("ws1");
    expect(winOf("ws1")).toBeUndefined();
  });

  it("close de janela inexistente é no-op", () => {
    s().open("ws1", "a.ts");
    s().close("ghost");
    expect(s().windows).toHaveLength(1);
  });

  it("close não afeta outras janelas", () => {
    s().open("ws1", "a.ts");
    s().open("ws2", "a.ts");
    s().close("ws1");
    expect(winOf("ws2")).toBeDefined();
  });
});

describe("windows-store — closeTab", () => {
  it("closeTab remove a aba", () => {
    s().open("ws1", "a.ts");
    s().open("ws1", "b.ts");
    s().closeTab("ws1", "a.ts");
    expect(winOf("ws1")?.tabs).toEqual(["b.ts"]);
  });

  it("closeTab da última aba fecha a janela", () => {
    s().open("ws1", "a.ts");
    s().closeTab("ws1", "a.ts");
    expect(winOf("ws1")).toBeUndefined();
  });

  it("closeTab na aba ativa ativa a aba anterior", () => {
    s().open("ws1", "a.ts");
    s().open("ws1", "b.ts");
    s().open("ws1", "c.ts");
    s().closeTab("ws1", "c.ts");
    expect(winOf("ws1")?.activeTab).toBe("b.ts");
  });

  it("closeTab na primeira aba ativa a próxima", () => {
    s().open("ws1", "a.ts");
    s().open("ws1", "b.ts");
    s().setActiveTab("ws1", "a.ts");
    s().closeTab("ws1", "a.ts");
    expect(winOf("ws1")?.activeTab).toBe("b.ts");
  });

  it("closeTab em aba não-ativa não muda a aba ativa", () => {
    s().open("ws1", "a.ts");
    s().open("ws1", "b.ts");
    s().closeTab("ws1", "a.ts");
    expect(winOf("ws1")?.activeTab).toBe("b.ts");
  });

  it("closeTab de janela inexistente é no-op", () => {
    s().open("ws1", "a.ts");
    expect(() => s().closeTab("ghost", "a.ts")).not.toThrow();
    expect(s().windows).toHaveLength(1);
  });
});

describe("windows-store — setActiveTab", () => {
  it("setActiveTab troca a aba ativa", () => {
    s().open("ws1", "a.ts");
    s().open("ws1", "b.ts");
    s().setActiveTab("ws1", "a.ts");
    expect(winOf("ws1")?.activeTab).toBe("a.ts");
    expect(winOf("ws1")?.title).toBe("a.ts");
  });

  it("setActiveTab com path não existente na janela é no-op", () => {
    s().open("ws1", "a.ts");
    s().setActiveTab("ws1", "nonexistent.ts");
    expect(winOf("ws1")?.activeTab).toBe("a.ts");
  });
});

describe("windows-store — focus / z-order", () => {
  it("focus traz a janela para o topo", () => {
    s().open("ws1", "a.ts");
    s().open("ws2", "b.ts");
    s().focus("ws1");
    expect(winOf("ws1")?.zIndex).toBe(s().topZ);
  });

  it("focus na janela já no topo é no-op", () => {
    s().open("ws1", "a.ts");
    const topZBefore = s().topZ;
    s().focus("ws1");
    expect(s().topZ).toBe(topZBefore);
  });

  it("focus em janela inexistente é no-op", () => {
    s().open("ws1", "a.ts");
    const before = s().topZ;
    s().focus("ghost");
    expect(s().topZ).toBe(before);
  });

  it("topZ cresce monotonicamente ao focar", () => {
    s().open("ws1", "a.ts");
    s().open("ws2", "b.ts");
    const z1 = s().topZ;
    s().focus("ws1");
    expect(s().topZ).toBeGreaterThan(z1);
  });
});

describe("windows-store — minimize / restore", () => {
  it("minimize marca minimized=true", () => {
    s().open("ws1", "a.ts");
    s().minimize("ws1");
    expect(winOf("ws1")?.minimized).toBe(true);
  });

  it("minimize de inexistente não quebra", () => {
    s().open("ws1", "a.ts");
    expect(() => s().minimize("ghost")).not.toThrow();
    expect(s().windows).toHaveLength(1);
  });

  it("restore marca minimized=false e traz pro topo", () => {
    s().open("ws1", "a.ts");
    s().minimize("ws1");
    s().restore("ws1");
    const w = winOf("ws1")!;
    expect(w.minimized).toBe(false);
    expect(w.zIndex).toBe(s().topZ);
  });

  it("restore incrementa topZ", () => {
    s().open("ws1", "a.ts");
    const before = s().topZ;
    s().restore("ws1");
    expect(s().topZ).toBe(before + 1);
  });
});

describe("windows-store — setBounds", () => {
  it("setBounds atualiza posição e tamanho", () => {
    s().open("ws1", "a.ts");
    s().setBounds("ws1", { x: 10, y: 20, w: 300, h: 200 });
    expect(winOf("ws1")).toMatchObject({ x: 10, y: 20, w: 300, h: 200 });
  });

  it("setBounds parcial atualiza só os campos dados", () => {
    s().open("ws1", "a.ts");
    s().setBounds("ws1", { x: 5 });
    const w = winOf("ws1")!;
    expect(w.x).toBe(5);
    expect(w.w).toBe(640);
  });

  it("setBounds de janela inexistente é no-op", () => {
    s().open("ws1", "a.ts");
    s().setBounds("ghost", { x: 999 });
    expect(winOf("ws1")?.x).not.toBe(999);
  });
});

// ── Fluxo multi-arquivo (Part F): 2+ arquivos = 1 janela com abas ─────────────

describe("windows-store — fluxo multi-arquivo (abas)", () => {
  it("abrir 2 arquivos no mesmo workspace → 1 janela com 2 abas", () => {
    s().open("ws1", "a.ts");
    s().open("ws1", "b.ts");
    expect(s().windows).toHaveLength(1);
    expect(winOf("ws1")?.tabs).toEqual(["a.ts", "b.ts"]);
  });

  it("abrir 3 arquivos → 3 abas na ordem de abertura, último ativo", () => {
    s().open("ws1", "a.ts");
    s().open("ws1", "b.ts");
    s().open("ws1", "c.ts");
    const w = winOf("ws1");
    expect(w?.tabs).toEqual(["a.ts", "b.ts", "c.ts"]);
    expect(w?.activeTab).toBe("c.ts");
  });

  it("alternar entre abas via setActiveTab", () => {
    s().open("ws1", "a.ts");
    s().open("ws1", "b.ts");
    s().setActiveTab("ws1", "a.ts");
    expect(winOf("ws1")?.activeTab).toBe("a.ts");
    expect(winOf("ws1")?.title).toBe("a.ts");
  });

  it("fechar a aba ativa volta para a anterior", () => {
    s().open("ws1", "a.ts");
    s().open("ws1", "b.ts");
    s().closeTab("ws1", "b.ts");
    expect(winOf("ws1")?.tabs).toEqual(["a.ts"]);
    expect(winOf("ws1")?.activeTab).toBe("a.ts");
  });

  it("fechar a última aba fecha a janela inteira", () => {
    s().open("ws1", "a.ts");
    s().closeTab("ws1", "a.ts");
    expect(winOf("ws1")).toBeUndefined();
  });

  it("reabrir um arquivo já aberto não duplica a aba", () => {
    s().open("ws1", "a.ts");
    s().open("ws1", "b.ts");
    s().open("ws1", "a.ts");
    expect(winOf("ws1")?.tabs).toEqual(["a.ts", "b.ts"]);
    expect(winOf("ws1")?.activeTab).toBe("a.ts");
  });
});

describe("windows-store — closeAll (reset de nova conversa / delete)", () => {
  it("fecha todas as janelas de uma vez", () => {
    s().open("ws1", "a.ts");
    s().open("ws2", "b.ts");
    s().open("ws3", "c.ts");
    expect(s().windows).toHaveLength(3);
    s().closeAll();
    expect(s().windows).toHaveLength(0);
  });

  it("closeAll sem janelas é no-op (não quebra)", () => {
    expect(s().windows).toHaveLength(0);
    s().closeAll();
    expect(s().windows).toHaveLength(0);
  });

  it("closeAll também zera o editor docked (modo IDE) — não deixa arquivo de workspace anterior vazando", () => {
    s().openDocked("ws1", "src/main.ts");
    expect(s().dockedWorkspaceId).toBe("ws1");
    s().closeAll();
    expect(s().dockedWorkspaceId).toBeNull();
    expect(s().dockedTabs).toEqual([]);
    expect(s().dockedActiveTab).toBeNull();
  });
});

describe("windows-store — persistência não vaza estado docked entre workspaces", () => {
  it("partialize exclui dockedWorkspaceId/dockedTabs/dockedActiveTab do que seria salvo", () => {
    s().open("ws1", "a.ts");
    s().openDocked("ws1", "src/main.ts");

    const partialize = useWindowsStore.persist.getOptions().partialize;
    expect(partialize).toBeDefined();
    const persisted = partialize?.(s()) as Record<string, unknown>;

    expect(persisted).not.toHaveProperty("dockedWorkspaceId");
    expect(persisted).not.toHaveProperty("dockedTabs");
    expect(persisted).not.toHaveProperty("dockedActiveTab");
    expect(persisted).toHaveProperty("windows");
    expect(persisted).toHaveProperty("topZ");
    expect(persisted).toHaveProperty("canvasDocuments");
    expect(persisted).toHaveProperty("activeCanvasDocumentId");
  });
});

describe("windows-store — documentos do Canvas", () => {
  it("preserva documentos de outros workspaces ao abrir um documento", () => {
    s().openCanvasDocument({
      id: "plan:ws1:t1:a",
      kind: "plan",
      workspaceId: "ws1",
      threadId: "t1",
      title: "A",
    });
    s().openCanvasDocument({
      id: "plan:ws2:t2:b",
      kind: "plan",
      workspaceId: "ws2",
      threadId: "t2",
      title: "B",
    });
    expect(s().canvasDocuments.map((document) => document.id)).toEqual([
      "plan:ws1:t1:a",
      "plan:ws2:t2:b",
    ]);
  });

  it("persiste documentos com payload que podem ser reidratados", () => {
    s().openCanvasDocument({
      id: "commit:ws1:t1:abc",
      kind: "commit-details",
      workspaceId: "ws1",
      threadId: "t1",
      title: "Commit",
    });
    const partialize = useWindowsStore.persist.getOptions().partialize;
    const persisted = partialize?.(s()) as Record<string, unknown>;
    expect(persisted.canvasDocuments).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "commit:ws1:t1:abc" }),
      ]),
    );
    expect(persisted.activeCanvasDocumentId).toBe("commit:ws1:t1:abc");
  });

  it("migra e preserva um documento de arquivo válido", () => {
    const migrate = useWindowsStore.persist.getOptions().migrate;
    expect(migrate).toBeDefined();

    const migrated = migrate?.(
      {
        windows: [],
        topZ: 120,
        canvasDocuments: [
          {
            id: "file:ws1:src/main.ts",
            kind: "file",
            workspaceId: "ws1",
            title: "main.ts",
            path: "src/main.ts",
          },
        ],
        activeCanvasDocumentId: "file:ws1:src/main.ts",
      },
      0,
    ) as Record<string, unknown>;

    expect(migrated.canvasDocuments).toEqual([
      expect.objectContaining({ id: "file:ws1:src/main.ts" }),
    ]);
    expect(migrated.activeCanvasDocumentId).toBe("file:ws1:src/main.ts");
  });

  it("rejeita documentos com tipo não suportado", () => {
    const migrate = useWindowsStore.persist.getOptions().migrate!;
    const migrated = migrate(
      {
        canvasDocuments: [
          { id: "bad", kind: "unknown", workspaceId: "ws", title: "Bad" },
        ],
        activeCanvasDocumentId: "bad",
      },
      0,
    ) as Record<string, unknown>;
    expect(migrated.canvasDocuments).toEqual([]);
    expect(migrated.activeCanvasDocumentId).toBeNull();
  });

  it("descarta payloads de Canvas não-array e entradas nulas", () => {
    const migrate = useWindowsStore.persist.getOptions().migrate!;
    const migrated = migrate(
      {
        canvasDocuments: [
          null,
          1,
          "bad",
          {
            id: "ok",
            kind: "file",
            workspaceId: "ws",
            title: "Ok",
            path: "a.ts",
          },
        ],
        activeCanvasDocumentId: "ok",
      },
      0,
    ) as Record<string, unknown>;
    expect(migrated.canvasDocuments).toEqual([
      expect.objectContaining({ id: "ok" }),
    ]);
    expect(migrated.activeCanvasDocumentId).toBe("ok");

    const nonArray = migrate({ canvasDocuments: null }, 0) as Record<
      string,
      unknown
    >;
    expect(nonArray.canvasDocuments).toEqual([]);
  });

  it("fecha modal sem ativar automaticamente outro documento", () => {
    s().openCanvasDocument({
      id: "a",
      kind: "plan",
      workspaceId: "ws",
      title: "A",
    });
    s().openCanvasDocument({
      id: "b",
      kind: "plan",
      workspaceId: "ws",
      title: "B",
    });
    s().closeCanvasDocumentModal("b");
    expect(s().activeCanvasDocumentId).toBeNull();
    expect(s().canvasDocuments.map((document) => document.id)).toEqual(["a"]);
  });
});

describe("windows-store — docked editor (IDE mode)", () => {
  it("fecha uma aba de arquivo no Canvas e no editor docked juntos", () => {
    s().openDocked("ws1", "src/main.ts");
    s().openDocked("ws1", "src/utils.ts");
    s().setDockedActiveTab("src/main.ts");

    s().closeCanvasDocumentAndDockedTab("file:ws1:src/main.ts");

    expect(s().dockedTabs).toEqual(["src/utils.ts"]);
    expect(s().dockedActiveTab).toBe("src/utils.ts");
    expect(s().canvasDocuments.map((document) => document.id)).toEqual([
      "file:ws1:src/utils.ts",
    ]);
    expect(s().activeCanvasDocumentId).toBe("file:ws1:src/utils.ts");
  });

  it("ignora um identificador vazio sem alterar Canvas nem editor docked", () => {
    s().openDocked("ws1", "src/main.ts");
    s().openDocked("ws1", "src/utils.ts");
    s().setDockedActiveTab("src/main.ts");

    const before = {
      dockedWorkspaceId: s().dockedWorkspaceId,
      dockedTabs: [...s().dockedTabs],
      dockedActiveTab: s().dockedActiveTab,
      canvasDocuments: [...s().canvasDocuments],
      activeCanvasDocumentId: s().activeCanvasDocumentId,
    };

    s().closeCanvasDocumentAndDockedTab("");

    expect(s().dockedWorkspaceId).toBe(before.dockedWorkspaceId);
    expect(s().dockedTabs).toEqual(before.dockedTabs);
    expect(s().dockedActiveTab).toBe(before.dockedActiveTab);
    expect(s().canvasDocuments).toEqual(before.canvasDocuments);
    expect(s().activeCanvasDocumentId).toBe(before.activeCanvasDocumentId);
  });

  it("openDocked — workspace novo: inicializa com workspace + tab", () => {
    s().openDocked("ws1", "src/main.ts");
    expect(s().dockedWorkspaceId).toBe("ws1");
    expect(s().dockedTabs).toEqual(["src/main.ts"]);
    expect(s().dockedActiveTab).toBe("src/main.ts");
  });

  it("openDocked — mesmo workspace, path novo: adiciona tab e ativa", () => {
    s().openDocked("ws1", "src/main.ts");
    s().openDocked("ws1", "src/utils.ts");
    expect(s().dockedTabs).toEqual(["src/main.ts", "src/utils.ts"]);
    expect(s().dockedActiveTab).toBe("src/utils.ts");
  });

  it("openDocked — mesmo workspace, path já aberto: só ativa sem duplicar", () => {
    s().openDocked("ws1", "src/main.ts");
    s().openDocked("ws1", "src/utils.ts");
    s().openDocked("ws1", "src/main.ts");
    expect(s().dockedTabs).toEqual(["src/main.ts", "src/utils.ts"]);
    expect(s().dockedActiveTab).toBe("src/main.ts");
  });

  it("openDocked — workspace diferente: reseta tabs para o novo workspace", () => {
    s().openDocked("ws1", "src/main.ts");
    s().openDocked("ws2", "src/app.ts");
    expect(s().dockedWorkspaceId).toBe("ws2");
    expect(s().dockedTabs).toEqual(["src/app.ts"]);
    expect(s().dockedActiveTab).toBe("src/app.ts");
  });

  it("setDockedActiveTab — ativa tab existente", () => {
    s().openDocked("ws1", "src/main.ts");
    s().openDocked("ws1", "src/utils.ts");
    s().setDockedActiveTab("src/main.ts");
    expect(s().dockedActiveTab).toBe("src/main.ts");
  });

  it("setDockedActiveTab — path inexistente é no-op", () => {
    s().openDocked("ws1", "src/main.ts");
    s().setDockedActiveTab("nao-existe.ts");
    expect(s().dockedActiveTab).toBe("src/main.ts");
  });

  it("closeDockedTab — última tab: zera workspaceId, tabs e activeTab", () => {
    s().openDocked("ws1", "src/main.ts");
    s().closeDockedTab("src/main.ts");
    expect(s().dockedWorkspaceId).toBeNull();
    expect(s().dockedTabs).toEqual([]);
    expect(s().dockedActiveTab).toBeNull();
  });

  it("closeDockedTab — aba ativa removida: ativa a anterior", () => {
    s().openDocked("ws1", "a.ts");
    s().openDocked("ws1", "b.ts");
    s().openDocked("ws1", "c.ts");
    s().closeDockedTab("c.ts");
    expect(s().dockedTabs).toEqual(["a.ts", "b.ts"]);
    expect(s().dockedActiveTab).toBe("b.ts");
  });

  it("closeDockedTab — primeira aba ativa removida: ativa a próxima", () => {
    s().openDocked("ws1", "a.ts");
    s().openDocked("ws1", "b.ts");
    s().setDockedActiveTab("a.ts");
    s().closeDockedTab("a.ts");
    expect(s().dockedActiveTab).toBe("b.ts");
  });

  it("closeDockedTab — aba não-ativa removida: ativa permanece inalterada", () => {
    s().openDocked("ws1", "a.ts");
    s().openDocked("ws1", "b.ts");
    s().closeDockedTab("a.ts");
    expect(s().dockedActiveTab).toBe("b.ts");
    expect(s().dockedTabs).toEqual(["b.ts"]);
  });
});

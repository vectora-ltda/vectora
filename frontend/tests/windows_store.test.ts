/**
 * windows-store (workstation flutuante com abas por workspace)
 *
 * Cobre open/closeTab/setActiveTab/close/focus/minimize/restore/setBounds
 * e o z-order incremental. O store é singleton por módulo — reseta via
 * setState entre casos.
 */

import { describe, it, expect, beforeEach } from "vitest";
import { useWindowsStore } from "../lib/stores/windows-store";

const WS = "ws-1";
const PATH_A = "src/main.ts";
const PATH_B = "README.md";

function reset() {
  useWindowsStore.setState({ windows: [], topZ: 100 });
}

function open(wsId = WS, path = PATH_A) {
  useWindowsStore.getState().open(wsId, path);
}

describe("windows-store", () => {
  beforeEach(reset);

  // ── open ────────────────────────────────────────────────────────────────────

  it("open — cria janela com id = workspaceId", () => {
    open();
    const { windows } = useWindowsStore.getState();
    expect(windows).toHaveLength(1);
    const win = windows[0];
    expect(win.id).toBe(WS);
    expect(win.workspaceId).toBe(WS);
    expect(win.tabs).toEqual([PATH_A]);
    expect(win.activeTab).toBe(PATH_A);
    expect(win.title).toBe("main.ts");
    expect(win.minimized).toBe(false);
    expect(win.w).toBe(640);
    expect(win.h).toBe(460);
  });

  it("open — incrementa topZ a cada janela nova (workspace diferente)", () => {
    open("ws-A", PATH_A);
    const z1 = useWindowsStore.getState().topZ;
    open("ws-B", PATH_B);
    const z2 = useWindowsStore.getState().topZ;
    expect(z2).toBeGreaterThan(z1);
  });

  it("open — cascata: posição diferente para janelas de workspaces distintos", () => {
    open("ws-A", PATH_A);
    open("ws-B", PATH_B);
    const [a, b] = useWindowsStore.getState().windows;
    expect(b.x).toBeGreaterThan(a.x);
    expect(b.y).toBeGreaterThan(a.y);
  });

  it("open — segundo arquivo no mesmo workspace vira aba (não nova janela)", () => {
    open(WS, PATH_A);
    open(WS, PATH_B);
    const { windows } = useWindowsStore.getState();
    expect(windows).toHaveLength(1);
    expect(windows[0].tabs).toEqual([PATH_A, PATH_B]);
    expect(windows[0].activeTab).toBe(PATH_B);
  });

  it("open — reabre aba existente: ativa sem duplicar", () => {
    open(WS, PATH_A);
    open(WS, PATH_B);
    open(WS, PATH_A);
    const { windows } = useWindowsStore.getState();
    expect(windows).toHaveLength(1);
    expect(windows[0].tabs).toHaveLength(2);
    expect(windows[0].activeTab).toBe(PATH_A);
  });

  it("open — restaura e foca janela minimizada", () => {
    open();
    useWindowsStore.getState().minimize(WS);
    open();
    expect(useWindowsStore.getState().windows[0].minimized).toBe(false);
  });

  it("open — title usa basename do path", () => {
    useWindowsStore.getState().open(WS, "deep/nested/file.tsx");
    const win = useWindowsStore.getState().windows[0];
    expect(win.title).toBe("file.tsx");
  });

  // ── close ───────────────────────────────────────────────────────────────────

  it("close — remove a janela inteira pelo id", () => {
    open("ws-A", PATH_A);
    open("ws-B", PATH_A);
    useWindowsStore.getState().close("ws-A");
    const { windows } = useWindowsStore.getState();
    expect(windows).toHaveLength(1);
    expect(windows[0].id).toBe("ws-B");
  });

  it("close — id inexistente não altera lista", () => {
    open();
    useWindowsStore.getState().close("nao-existe");
    expect(useWindowsStore.getState().windows).toHaveLength(1);
  });

  // ── closeTab ─────────────────────────────────────────────────────────────────

  it("closeTab — remove a aba e mantém as demais", () => {
    open(WS, PATH_A);
    open(WS, PATH_B);
    useWindowsStore.getState().closeTab(WS, PATH_A);
    const win = useWindowsStore.getState().windows[0];
    expect(win.tabs).toEqual([PATH_B]);
  });

  it("closeTab — última aba fecha a janela", () => {
    open();
    useWindowsStore.getState().closeTab(WS, PATH_A);
    expect(useWindowsStore.getState().windows).toHaveLength(0);
  });

  it("closeTab — aba ativa fechada ativa a anterior", () => {
    open(WS, PATH_A);
    open(WS, PATH_B);
    useWindowsStore.getState().closeTab(WS, PATH_B);
    expect(useWindowsStore.getState().windows[0].activeTab).toBe(PATH_A);
  });

  // ── setActiveTab ─────────────────────────────────────────────────────────────

  it("setActiveTab — troca a aba ativa", () => {
    open(WS, PATH_A);
    open(WS, PATH_B);
    useWindowsStore.getState().setActiveTab(WS, PATH_A);
    const win = useWindowsStore.getState().windows[0];
    expect(win.activeTab).toBe(PATH_A);
    expect(win.title).toBe("main.ts");
  });

  // ── focus ───────────────────────────────────────────────────────────────────

  it("focus — traz a janela ao topo (zIndex maior que as demais)", () => {
    open("ws-A", PATH_A);
    open("ws-B", PATH_B);
    useWindowsStore.getState().focus("ws-A");
    const { windows, topZ } = useWindowsStore.getState();
    const a = windows.find((w) => w.id === "ws-A")!;
    expect(a.zIndex).toBe(topZ);
  });

  it("focus — não altera estado se já estiver no topo", () => {
    open(WS, PATH_A);
    const before = useWindowsStore.getState().topZ;
    useWindowsStore.getState().focus(WS);
    expect(useWindowsStore.getState().topZ).toBe(before);
  });

  // ── minimize / restore ──────────────────────────────────────────────────────

  it("minimize — marca a janela como minimized", () => {
    open();
    useWindowsStore.getState().minimize(WS);
    expect(useWindowsStore.getState().windows[0].minimized).toBe(true);
  });

  it("restore — desmarca minimized e incrementa zIndex", () => {
    open();
    useWindowsStore.getState().minimize(WS);
    const zBefore = useWindowsStore.getState().topZ;
    useWindowsStore.getState().restore(WS);
    const win = useWindowsStore.getState().windows[0];
    expect(win.minimized).toBe(false);
    expect(win.zIndex).toBeGreaterThan(zBefore);
  });

  // ── setBounds ───────────────────────────────────────────────────────────────

  it("setBounds — atualiza x/y sem afetar outras propriedades", () => {
    open();
    useWindowsStore.getState().setBounds(WS, { x: 200, y: 150 });
    const win = useWindowsStore.getState().windows[0];
    expect(win.x).toBe(200);
    expect(win.y).toBe(150);
    expect(win.w).toBe(640);
  });

  it("setBounds — atualiza w/h", () => {
    open();
    useWindowsStore.getState().setBounds(WS, { w: 800, h: 600 });
    const win = useWindowsStore.getState().windows[0];
    expect(win.w).toBe(800);
    expect(win.h).toBe(600);
  });

  it("setBounds — id inexistente não altera lista", () => {
    open();
    useWindowsStore.getState().setBounds("nao-existe", { x: 999 });
    expect(useWindowsStore.getState().windows[0].x).not.toBe(999);
  });

  // ── múltiplas janelas ────────────────────────────────────────────────────────

  it("múltiplos workspaces — janelas isoladas por workspaceId", () => {
    open("ws-A", PATH_A);
    open("ws-B", PATH_A);
    const { windows } = useWindowsStore.getState();
    expect(windows).toHaveLength(2);
    expect(windows[0].workspaceId).toBe("ws-A");
    expect(windows[1].workspaceId).toBe("ws-B");
  });
});

// @vitest-environment jsdom
/**
 * ThreadItem — menu de contexto (right-click) com Renomear/Fixar/Apagar.
 * Cobre: abre com os 3 itens; Renomear vira inline-edit e confirma no Enter
 * (não confirma com título vazio); Fixar/Desafixar alterna a label conforme
 * thread.pinned; Apagar dispara o mesmo onDelete do ícone de hover.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  render,
  screen,
  cleanup,
  fireEvent,
  act,
} from "@testing-library/react";
import type { Thread } from "@/lib/hooks/threads";
import { ThreadItem } from "../thread-item";

vi.mock("@/lib/stores/streaming-store", () => ({
  useStreamingStore: () => false,
}));

vi.mock("../../../src/router", () => ({
  queryClient: { prefetchQuery: vi.fn() },
}));
vi.mock("@/lib/api/vectora-client", () => ({
  getHistory: vi.fn(),
  listThreads: vi.fn(),
}));
vi.mock("@/lib/queries/threads", () => ({
  threadsQueryKey: (limit = 100) => ["threads", limit],
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: {
    workbench_close: () => "Fechar",
    sidebar_new_conversation: () => "Nova conversa",
    sidebar_ctx_rename: () => "Renomear",
    sidebar_ctx_pin: () => "Fixar",
    sidebar_ctx_unpin: () => "Desafixar",
    sidebar_ctx_delete: () => "Apagar",
    sidebar_rename_placeholder: () => "Nome da sessão",
    sidebar_delete_thread: () => "Excluir conversa",
  },
}));

afterEach(cleanup);
beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

function makeThread(overrides: Partial<Thread> = {}): Thread {
  return {
    thread_id: "t1",
    metadata: { user_id: "local", title: "Conversa T1" },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  } as unknown as Thread;
}

describe("ThreadItem — menu de contexto", () => {
  it("right-click abre o menu com Renomear/Fixar/Apagar", () => {
    render(
      <ThreadItem
        thread={makeThread()}
        isActive={false}
        onSelect={vi.fn()}
        onDelete={vi.fn()}
        onRename={vi.fn()}
        onTogglePin={vi.fn()}
      />,
    );

    fireEvent.contextMenu(screen.getByText("Conversa T1"));

    expect(screen.getByRole("menuitem", { name: "Renomear" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Fixar" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Apagar" })).toBeTruthy();
  });

  it("thread já fixada mostra 'Desafixar' em vez de 'Fixar'", () => {
    render(
      <ThreadItem
        thread={makeThread({ pinned: true })}
        isActive={false}
        onSelect={vi.fn()}
        onDelete={vi.fn()}
        onRename={vi.fn()}
        onTogglePin={vi.fn()}
      />,
    );

    fireEvent.contextMenu(screen.getByText("Conversa T1"));

    expect(screen.getByRole("menuitem", { name: "Desafixar" })).toBeTruthy();
    expect(
      screen.queryByRole("menuitem", { name: "Fixar" }),
    ).not.toBeInTheDocument();
  });

  it("clicar em 'Fixar' chama onTogglePin com o novo estado", () => {
    const onTogglePin = vi.fn();
    render(
      <ThreadItem
        thread={makeThread({ pinned: false })}
        isActive={false}
        onSelect={vi.fn()}
        onDelete={vi.fn()}
        onRename={vi.fn()}
        onTogglePin={onTogglePin}
      />,
    );

    fireEvent.contextMenu(screen.getByText("Conversa T1"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Fixar" }));

    expect(onTogglePin).toHaveBeenCalledWith("t1", true);
  });

  it("clicar em 'Apagar' no menu chama onDelete", () => {
    const onDelete = vi.fn();
    render(
      <ThreadItem
        thread={makeThread()}
        isActive={false}
        onSelect={vi.fn()}
        onDelete={onDelete}
        onRename={vi.fn()}
        onTogglePin={vi.fn()}
      />,
    );

    fireEvent.contextMenu(screen.getByText("Conversa T1"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Apagar" }));

    expect(onDelete).toHaveBeenCalledWith("t1", expect.anything());
  });

  it("clicar em 'Renomear' abre um input com o título atual; Enter confirma via onRename", () => {
    const onRename = vi.fn();
    render(
      <ThreadItem
        thread={makeThread()}
        isActive={false}
        onSelect={vi.fn()}
        onDelete={vi.fn()}
        onRename={onRename}
        onTogglePin={vi.fn()}
      />,
    );

    fireEvent.contextMenu(screen.getByText("Conversa T1"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Renomear" }));

    const input = screen.getByDisplayValue("Conversa T1") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Novo título" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onRename).toHaveBeenCalledWith("t1", "Novo título");
  });

  it("erro/borda: confirmar renomear com título vazio (só espaços) não chama onRename", () => {
    const onRename = vi.fn();
    render(
      <ThreadItem
        thread={makeThread()}
        isActive={false}
        onSelect={vi.fn()}
        onDelete={vi.fn()}
        onRename={onRename}
        onTogglePin={vi.fn()}
      />,
    );

    fireEvent.contextMenu(screen.getByText("Conversa T1"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Renomear" }));

    const input = screen.getByDisplayValue("Conversa T1") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onRename).not.toHaveBeenCalled();
  });

  it("erro/borda: Escape cancela a edição sem chamar onRename", () => {
    const onRename = vi.fn();
    render(
      <ThreadItem
        thread={makeThread()}
        isActive={false}
        onSelect={vi.fn()}
        onDelete={vi.fn()}
        onRename={onRename}
        onTogglePin={vi.fn()}
      />,
    );

    fireEvent.contextMenu(screen.getByText("Conversa T1"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Renomear" }));

    const input = screen.getByDisplayValue("Conversa T1") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Vai ser cancelado" } });
    fireEvent.keyDown(input, { key: "Escape" });

    expect(onRename).not.toHaveBeenCalled();
    expect(screen.getByText("Conversa T1")).toBeInTheDocument();
  });

  it("long-press touch abre ações e não seleciona a conversa", () => {
    const onSelect = vi.fn();
    render(
      <ThreadItem
        thread={makeThread()}
        isActive={false}
        onSelect={onSelect}
        onDelete={vi.fn()}
        onRename={vi.fn()}
        onTogglePin={vi.fn()}
      />,
    );
    const row = screen.getByText("Conversa T1").parentElement?.parentElement;
    expect(row).toBeTruthy();
    fireEvent.touchStart(row!, { touches: [{ clientY: 100 }] });
    act(() => vi.advanceTimersByTime(500));
    expect(screen.getAllByText("Renomear").length).toBeGreaterThan(1);
    fireEvent.touchEnd(row!, { changedTouches: [{ clientY: 100 }] });
    fireEvent.click(row!);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("movimento cancela long-press e mantém seleção no click", () => {
    const onSelect = vi.fn();
    render(
      <ThreadItem
        thread={makeThread()}
        isActive={false}
        onSelect={onSelect}
        onDelete={vi.fn()}
        onRename={vi.fn()}
        onTogglePin={vi.fn()}
      />,
    );
    const row = screen.getByText("Conversa T1").parentElement?.parentElement;
    fireEvent.touchStart(row!, { touches: [{ clientY: 100 }] });
    fireEvent.touchMove(row!, { touches: [{ clientY: 130 }] });
    vi.advanceTimersByTime(500);
    expect(screen.queryByText("Renomear")).not.toBeInTheDocument();
    fireEvent.click(row!);
    expect(onSelect).toHaveBeenCalledWith("t1");
  });
});

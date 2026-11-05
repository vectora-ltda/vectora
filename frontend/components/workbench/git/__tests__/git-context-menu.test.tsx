// @vitest-environment jsdom
/**
 * Testes do useContextMenu — menu flutuante de clique direito compartilhado
 * pelo painel Git (arquivos e commits).
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { useContextMenu, type ContextMenuItem } from "../git-context-menu";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function Harness({ items }: { items: ContextMenuItem[] }) {
  const menu = useContextMenu();
  return (
    <div>
      <div data-testid="target" onContextMenu={(e) => menu.open(e, items)}>
        alvo
      </div>
      {menu.element}
    </div>
  );
}

describe("useContextMenu", () => {
  it("não abre o menu quando a lista de itens está vazia", () => {
    render(<Harness items={[]} />);
    fireEvent.contextMenu(screen.getByTestId("target"));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("abre o menu com os itens passados ao clicar com botão direito", () => {
    const onSelect = vi.fn();
    render(<Harness items={[{ label: "Stage", onSelect }]} />);
    fireEvent.contextMenu(screen.getByTestId("target"));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.getByText("Stage")).toBeInTheDocument();
  });

  it("clicar num item chama onSelect e fecha o menu", () => {
    const onSelect = vi.fn();
    render(<Harness items={[{ label: "Discard", onSelect }]} />);
    fireEvent.contextMenu(screen.getByTestId("target"));
    fireEvent.click(screen.getByText("Discard"));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("tecla Escape fecha o menu", () => {
    render(<Harness items={[{ label: "Unstage", onSelect: vi.fn() }]} />);
    fireEvent.contextMenu(screen.getByTestId("target"));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("clique fora do menu fecha, e item com danger=true recebe classe de destaque", () => {
    render(
      <Harness
        items={[{ label: "Discard", onSelect: vi.fn(), danger: true }]}
      />,
    );
    fireEvent.contextMenu(screen.getByTestId("target"));
    const item = screen.getByText("Discard");
    expect(item.className).toContain("text-destructive");

    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});

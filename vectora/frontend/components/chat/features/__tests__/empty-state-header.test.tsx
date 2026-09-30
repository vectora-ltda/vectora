// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

vi.mock("@/lib/paraglide/messages", () => ({
  m: {
    app_name: () => "Vectora",
    welcome_title: () => "O que posso fazer por você?",
    welcome_suggestion_1: () => "Sugestão 1",
    welcome_suggestion_2: () => "Sugestão 2",
    welcome_suggestion_3: () => "Sugestão 3",
  },
}));

vi.mock("@/lib/i18n-dyn", () => ({
  mDyn: (key: string) => key,
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: undefined }),
}));

vi.mock("@/lib/api/vectora-client", () => ({
  getStackHint: vi.fn(),
}));

// `next/image` resolve via o alias real de vitest.config.ts (src/shims/next-image.tsx)
// — não mockar aqui: um mock local ingênuo que espalha `...rest` direto no
// `<img>` reintroduz `priority` (boolean) como atributo DOM inválido, que o
// shim de verdade filtra corretamente.
import { EmptyStateHeader } from "../empty-state-header";

afterEach(cleanup);

describe("EmptyStateHeader", () => {
  it("renderiza o título de boas-vindas", () => {
    render(<EmptyStateHeader />);
    expect(screen.getByText("O que posso fazer por você?")).toBeInTheDocument();
  });

  it("não exibe CTAs de modo quando sem handlers (estado padrão de chat em andamento)", () => {
    render(<EmptyStateHeader />);
    expect(screen.queryByText("Chat")).toBeNull();
    expect(screen.queryByText("Sessão de código")).toBeNull();
  });

  it("não renderiza sugestões quando onSelect não é fornecido", () => {
    render(<EmptyStateHeader />);
    // mDyn retorna a chave; sugestões renderizam como "stack.unknown.1" etc.
    expect(screen.queryByText("stack.unknown.1")).toBeNull();
  });

  it("renderiza sugestões quando onSelect é fornecido e chama ao clicar", () => {
    const onSelect = vi.fn();
    render(<EmptyStateHeader onSelect={onSelect} />);
    const chip = screen.getByText("stack.unknown.1");
    fireEvent.click(chip);
    expect(onSelect).toHaveBeenCalledWith("stack.unknown.1");
  });

  it("mantém as sugestões em uma única coluna", () => {
    const onSelect = vi.fn();
    render(<EmptyStateHeader onSelect={onSelect} />);
    const suggestion = screen.getByText("stack.unknown.1");
    expect(suggestion.parentElement).toHaveClass("flex-col");
  });

  it("compacta a marca e o título no painel estreito do IDE", () => {
    render(<EmptyStateHeader compact onSelect={vi.fn()} />);
    expect(screen.getByText("Vectora").parentElement).toHaveClass("flex-col");
    expect(screen.getByText("O que posso fazer por você?")).toHaveClass(
      "text-lg",
    );
  });
});

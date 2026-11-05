// @vitest-environment jsdom
/**
 * ErrorBoundary: renderiza filhos normalmente; em erro de render mostra o
 * ErrorBanner recuperável (não propaga p/ derrubar a rota) e "tentar de novo"
 * reseta o estado.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

import { ErrorBoundary } from "../error-boundary";

afterEach(cleanup);

function Boom({ explode }: { explode: boolean }): React.ReactNode {
  if (explode) throw new Error("kaboom render");
  return <div>conteúdo ok</div>;
}

describe("ErrorBoundary", () => {
  it("renderiza os filhos quando não há erro", () => {
    render(
      <ErrorBoundary>
        <Boom explode={false} />
      </ErrorBoundary>,
    );
    expect(screen.getByText("conteúdo ok")).toBeInTheDocument();
  });

  it("captura erro de render e mostra o banner com a mensagem", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary>
        <Boom explode={true} />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("kaboom render")).toBeInTheDocument();
    spy.mockRestore();
  });

  it("usa fallbackMessage quando fornecida", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary fallbackMessage="mensagem amigável">
        <Boom explode={true} />
      </ErrorBoundary>,
    );
    expect(screen.getByText("mensagem amigável")).toBeInTheDocument();
    spy.mockRestore();
  });

  it("o botão de retry chama onReset", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const onReset = vi.fn();
    render(
      <ErrorBoundary onReset={onReset}>
        <Boom explode={true} />
      </ErrorBoundary>,
    );
    // O ErrorBanner expõe o botão de retry porque passamos onRetry interno.
    const retry = screen.getByRole("button");
    fireEvent.click(retry);
    expect(onReset).toHaveBeenCalled();
    spy.mockRestore();
  });
});

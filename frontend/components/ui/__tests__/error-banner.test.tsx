// @vitest-environment jsdom
/**
 * Tests para o ErrorBanner: mensagem, título, e ação de retry condicional.
 */

import { describe, expect, it, afterEach, vi } from "vitest";
import {
  render,
  screen,
  cleanup,
  fireEvent,
  waitFor,
} from "@testing-library/react";
import { ErrorBanner } from "../error-banner";

afterEach(cleanup);

describe("ErrorBanner", () => {
  it("renderiza a mensagem com role alert", () => {
    render(<ErrorBanner message="falha ao carregar" />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("falha ao carregar")).toBeInTheDocument();
  });

  it("usa o título custom quando fornecido", () => {
    render(<ErrorBanner message="x" title="Ops!" />);
    expect(screen.getByText("Ops!")).toBeInTheDocument();
  });

  it("não mostra botão de retry quando onRetry está ausente", () => {
    render(<ErrorBanner message="x" />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("chama onRetry ao clicar em tentar novamente", async () => {
    const onRetry = vi.fn().mockResolvedValue(undefined);
    render(<ErrorBanner message="x" onRetry={onRetry} />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(onRetry).toHaveBeenCalledTimes(1));
  });
});

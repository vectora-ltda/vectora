// @vitest-environment jsdom
/**
 * lazyWithRetry: em sucesso renderiza o componente e limpa a flag; na 1ª falha
 * (chunk obsoleto) recarrega a página uma vez; na 2ª falha (flag já setada)
 * propaga o erro em vez de loopar.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { Suspense } from "react";

import { lazyWithRetry } from "../lazy-with-retry";

const reloadMock = vi.fn();

beforeEach(() => {
  sessionStorage.clear();
  reloadMock.mockReset();
  Object.defineProperty(window, "location", {
    value: { reload: reloadMock },
    writable: true,
  });
});

afterEach(cleanup);

describe("lazyWithRetry", () => {
  it("renderiza o componente quando o import resolve", async () => {
    const Comp = lazyWithRetry(
      () => Promise.resolve({ default: () => <div>carregado</div> }),
      "ok",
    );
    render(
      <Suspense fallback={<div>loading</div>}>
        <Comp />
      </Suspense>,
    );
    await waitFor(() =>
      expect(screen.getByText("carregado")).toBeInTheDocument(),
    );
    expect(reloadMock).not.toHaveBeenCalled();
  });

  it("recarrega a página uma vez na 1ª falha de chunk", async () => {
    const Comp = lazyWithRetry(
      () =>
        Promise.reject(
          new Error("Failed to fetch dynamically imported module"),
        ),
      "stale",
    );
    render(
      <Suspense fallback={<div>loading</div>}>
        <Comp />
      </Suspense>,
    );
    await waitFor(() => expect(reloadMock).toHaveBeenCalledTimes(1));
    expect(sessionStorage.getItem("vectora-lazy-retry-stale")).toBe("1");
  });

  it("não recarrega de novo se a flag já está setada (evita loop)", async () => {
    sessionStorage.setItem("vectora-lazy-retry-stale2", "1");
    const Comp = lazyWithRetry(
      () => Promise.reject(new Error("still failing")),
      "stale2",
    );
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <Suspense fallback={<div>loading</div>}>
        <Comp />
      </Suspense>,
    );
    // Sem reload; a falha propaga (sem Suspense fallback infinito).
    await waitFor(() => expect(reloadMock).not.toHaveBeenCalled());
    spy.mockRestore();
  });
});

// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

let localizedSubtitle: string | undefined;

import Hero from "./Hero";

vi.mock("#/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get:
        (_t, prop) =>
        (..._args: unknown[]) =>
          prop === "hero_subtitle" && localizedSubtitle
            ? localizedSubtitle
            : String(prop),
    },
  ),
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    to,
    ...rest
  }: {
    children: React.ReactNode;
    to: string;
  } & React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

// Hero usa useLatestVersion (useQuery) pra exibir o badge de versão — precisa
// de um QueryClientProvider no render, senão useQueryClient lança.
function renderWithClient(ui: ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const fetchMock = vi.fn();

beforeEach(() => {
  localizedSubtitle = undefined;
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({ ok: false, json: async () => ({}) });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Hero", () => {
  it("renders the German subtitle with correct plural agreement", () => {
    const messages = JSON.parse(
      readFileSync(
        join(import.meta.dirname, "../../../messages/de.json"),
        "utf-8",
      ),
    ) as Record<string, string>;
    localizedSubtitle = messages.hero_subtitle;
    renderWithClient(<Hero />);
    expect(screen.getByText(localizedSubtitle)).toBeInTheDocument();
    expect(localizedSubtitle).toContain("Du und der Agent teilen");
    expect(
      screen.queryByText(/Du und der Agent teilt /),
    ).not.toBeInTheDocument();
  });

  it("mostra o gif de demo quando carrega com sucesso", () => {
    renderWithClient(<Hero />);
    const img = screen.getByRole("img", { name: "hero_gif_alt" });
    fireEvent.load(img);
    expect(img).toBeInTheDocument();
    expect(screen.queryByText("showcase_preview_soon")).not.toBeInTheDocument();
  });

  it("mostra o placeholder 'prévia em breve' quando o gif falha ao carregar", () => {
    renderWithClient(<Hero />);
    const img = screen.getByRole("img", { name: "hero_gif_alt" });
    fireEvent.error(img);
    expect(
      screen.queryByRole("img", { name: "hero_gif_alt" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("showcase_preview_soon")).toBeInTheDocument();
  });

  it("mostra o placeholder quando o gif já falhou antes da hidratação (evento error perdido)", () => {
    // O SSR entrega o <img> puro; se o GIF já 404 nesse meio-tempo, o
    // navegador dispara "error" antes do React anexar o onError — o
    // handler nunca roda. complete=true + naturalWidth=0 é o estado que o
    // navegador expõe nesse caso; sem o fallback via useEffect, a imagem
    // quebrada ficaria visível pra sempre (bug relatado em produção).
    const completeSpy = vi
      .spyOn(window.HTMLImageElement.prototype, "complete", "get")
      .mockReturnValue(true);
    const widthSpy = vi
      .spyOn(window.HTMLImageElement.prototype, "naturalWidth", "get")
      .mockReturnValue(0);

    renderWithClient(<Hero />);

    expect(
      screen.queryByRole("img", { name: "hero_gif_alt" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("showcase_preview_soon")).toBeInTheDocument();

    completeSpy.mockRestore();
    widthSpy.mockRestore();
  });
});

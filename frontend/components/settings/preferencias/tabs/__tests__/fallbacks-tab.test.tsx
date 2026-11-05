// @vitest-environment jsdom
/**
 * FallbacksTab — aba de ordem de fallback de modelos. Cobre: carregar a
 * ordem do backend, adicionar/remover um modelo da fila, e o caso de
 * borda "backend não devolve lista".
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  render,
  screen,
  cleanup,
  waitFor,
  fireEvent,
} from "@testing-library/react";

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_t, prop) => () => String(prop) }),
}));

import { FallbacksTab } from "../fallbacks-tab";
import {
  getAllowedModels,
  getModelDisplayName,
} from "@/lib/config/deployment-config";

beforeEach(() => {
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: false,
    media: q,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stubFetch(
  order: string[],
  opts: { ok?: boolean; imageFallback?: string } = {},
) {
  const { ok = true, imageFallback = "" } = opts;
  const patches: string[][] = [];
  const imagePatches: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      const method = init?.method ?? "GET";
      if (u.includes("/admin/model/fallback-order") && method === "GET") {
        return ok
          ? new Response(JSON.stringify({ fallback_order: order }))
          : new Response("erro", { status: 500 });
      }
      if (u.includes("/admin/model/fallback-order") && method === "PATCH") {
        const body = JSON.parse(String(init?.body)) as { order: string[] };
        patches.push(body.order);
        return new Response(JSON.stringify({ status: "updated" }));
      }
      if (u.includes("/admin/model/image-fallback") && method === "GET") {
        return new Response(JSON.stringify({ model: imageFallback }));
      }
      if (u.includes("/admin/model/image-fallback") && method === "PATCH") {
        const body = JSON.parse(String(init?.body)) as { model: string };
        imagePatches.push(body.model);
        return new Response(JSON.stringify({ status: "updated" }));
      }
      return new Response(JSON.stringify({}));
    }),
  );
  return { patches, imagePatches };
}

describe("FallbacksTab", () => {
  it("carrega a ordem do backend e renderiza cada modelo", async () => {
    const [first] = getAllowedModels();
    stubFetch([first]);

    render(<FallbacksTab />);

    await waitFor(() =>
      expect(
        screen.getByText("prefs_fallback_order_title"),
      ).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.queryAllByRole("listitem").length).toBe(1),
    );
  });

  it("lista vazia mostra o estado vazio, não uma lista em branco", async () => {
    stubFetch([]);

    render(<FallbacksTab />);

    await waitFor(() =>
      expect(
        screen.getByText("prefs_fallback_order_empty"),
      ).toBeInTheDocument(),
    );
  });

  it("remover um modelo da fila persiste a nova ordem via PATCH", async () => {
    const [first] = getAllowedModels();
    const { patches } = stubFetch([first]);

    render(<FallbacksTab />);
    await waitFor(() =>
      expect(screen.queryAllByRole("listitem").length).toBe(1),
    );

    fireEvent.click(screen.getByLabelText("prefs_fallback_order_remove"));

    await waitFor(() => expect(patches.at(-1)).toEqual([]));
  });

  it("backend sem lista (edge) não quebra a aba — fica vazio", async () => {
    stubFetch([], { ok: false });

    render(<FallbacksTab />);

    await waitFor(() =>
      expect(
        screen.getByText("prefs_fallback_order_empty"),
      ).toBeInTheDocument(),
    );
  });

  it("modelo de fallback de imagem carrega o valor salvo do backend", async () => {
    const [first] = getAllowedModels();
    stubFetch([], { imageFallback: first });

    render(<FallbacksTab />);

    await waitFor(() =>
      expect(
        screen.getByText("prefs_image_fallback_title"),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByText(getModelDisplayName(first) || first),
    ).toBeInTheDocument();
  });

  it("sem fallback configurado (edge), mostra a opção 'nenhum'", async () => {
    stubFetch([], { imageFallback: "" });

    render(<FallbacksTab />);

    await waitFor(() =>
      expect(screen.getByText("prefs_image_fallback_none")).toBeInTheDocument(),
    );
  });

  it("trocar o select do fallback de imagem persiste via PATCH", async () => {
    const [first, second] = getAllowedModels();
    const { imagePatches } = stubFetch([], { imageFallback: first });

    render(<FallbacksTab />);
    await waitFor(() =>
      expect(
        screen.getByText(getModelDisplayName(first) || first),
      ).toBeInTheDocument(),
    );

    const trigger = screen.getAllByRole("combobox")[0];
    fireEvent.click(trigger);
    const option = await screen.findByRole("option", {
      name: getModelDisplayName(second) || second,
    });
    fireEvent.click(option);

    await waitFor(() => expect(imagePatches.at(-1)).toBe(second));
  });
});

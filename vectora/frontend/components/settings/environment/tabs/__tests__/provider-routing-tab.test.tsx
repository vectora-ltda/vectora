// @vitest-environment jsdom
/**
 * Testes da ProviderRoutingTab (gateways Ollama e OpenRouter).
 *
 * Cobre:
 * - Ollama: lista de modelos registrados (vazia / populada), descoberta
 *   (host inacessível vs. host com modelos), registro/remoção (par de erro).
 * - OpenRouter: key não configurada mostra input; salvar key válida mostra
 *   badge configurada (par de erro: key rejeitada); busca no catálogo com
 *   key configurada e registro; remoção de key.
 */

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  render,
  screen,
  cleanup,
  fireEvent,
  waitFor,
  within,
} from "@testing-library/react";
import { ProviderRoutingTab } from "../provider-routing-tab";
import { overwriteGetLocale, baseLocale } from "@/lib/paraglide/runtime";

afterEach(cleanup);
afterEach(() => overwriteGetLocale(() => baseLocale));

function mockFetch(
  handlers: Partial<{
    registered: object[];
    discover: { reachable: boolean; models: object[] };
    registerOk: boolean;
    removeOk: boolean;
    openrouterStatus: { configured: boolean; masked: string };
    openrouterRegistered: object[];
    openrouterCatalog: object[];
    openrouterKeySaveOk: boolean;
    openrouterKeySaveDetail: string;
    openrouterRegisterOk: boolean;
    nineRouterStatus: {
      configured: boolean;
      base_url: string | null;
      masked: string;
    };
    nineRouterRegistered: object[];
    nineRouterDiscover: { reachable: boolean; models: object[] };
    nineRouterConfigSaveOk: boolean;
    nineRouterConfigSaveDetail: string;
    nineRouterRegisterOk: boolean;
    mediaModelsGet: { models: Record<string, string> } | null;
    mediaModelsPatch: { models: Record<string, string> };
    llamacppRuntimeStatus: {
      installed: boolean;
      path: string | null;
      files: string[];
      active_runtime?: string | null;
      runtimes: Array<{ id: string; asset: string }>;
    };
    llamacppRuntimeRemoveOk: boolean;
    hfModels?: object[];
    hfMetadata?: object;
  }>,
) {
  global.fetch = vi
    .fn()
    .mockImplementation((url: string, init?: RequestInit) => {
      const method = init?.method;

      if (url === "/provider-routing/ollama/registered" && !method) {
        return Promise.resolve({
          ok: true,
          json: async () => handlers.registered ?? [],
        } as Response);
      }
      if (url === "/provider-routing/ollama/models") {
        return Promise.resolve({
          ok: true,
          json: async () =>
            handlers.discover ?? { reachable: false, models: [] },
        } as Response);
      }
      if (url === "/provider-routing/ollama/registered" && method === "POST") {
        return Promise.resolve({
          ok: handlers.registerOk ?? true,
          status: handlers.registerOk === false ? 500 : 200,
          json: async () => ({
            id: "new-id",
            tag: "qwen3:8b",
            created_at: "now",
          }),
        } as Response);
      }
      if (
        typeof url === "string" &&
        url.startsWith("/provider-routing/ollama/registered/")
      ) {
        return Promise.resolve({
          ok: handlers.removeOk ?? true,
          status: handlers.removeOk === false ? 500 : 200,
          json: async () => ({ ok: true }),
        } as Response);
      }
      if (url === "/provider-routing/openrouter/status") {
        return Promise.resolve({
          ok: true,
          json: async () =>
            handlers.openrouterStatus ?? { configured: false, masked: "" },
        } as Response);
      }
      if (url === "/provider-routing/openrouter/key" && method === "POST") {
        const ok = handlers.openrouterKeySaveOk ?? true;
        return Promise.resolve({
          ok,
          status: ok ? 200 : 400,
          json: async () =>
            ok
              ? { configured: true, masked: "sk-or-•••cdef" }
              : { detail: handlers.openrouterKeySaveDetail ?? "Key rejeitada" },
        } as Response);
      }
      if (url === "/provider-routing/openrouter/key" && method === "DELETE") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ configured: false, masked: "" }),
        } as Response);
      }
      if (
        typeof url === "string" &&
        url.startsWith("/provider-routing/openrouter/models")
      ) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ models: handlers.openrouterCatalog ?? [] }),
        } as Response);
      }
      if (url === "/provider-routing/openrouter/registered" && !method) {
        return Promise.resolve({
          ok: true,
          json: async () => handlers.openrouterRegistered ?? [],
        } as Response);
      }
      if (
        url === "/provider-routing/openrouter/registered" &&
        method === "POST"
      ) {
        const ok = handlers.openrouterRegisterOk ?? true;
        return Promise.resolve({
          ok,
          status: ok ? 200 : 500,
          json: async () => ({
            id: "or-new-id",
            tag: "openai/gpt-4o",
            created_at: "now",
          }),
        } as Response);
      }
      if (
        typeof url === "string" &&
        url.startsWith("/provider-routing/openrouter/registered/")
      ) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ ok: true }),
        } as Response);
      }
      if (url === "/provider-routing/nine-router/status") {
        // Default: não configurado — a seção fica visível mas sem o botão
        // "Detectar modelos" (só aparece quando `status.configured`),
        // evitando ambiguidade com o mesmo botão da seção Ollama nos
        // testes que não são sobre 9Router.
        return Promise.resolve({
          ok: true,
          json: async () =>
            handlers.nineRouterStatus ?? {
              configured: false,
              base_url: null,
              masked: "",
            },
        } as Response);
      }
      if (url === "/provider-routing/llamacpp/status") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            configured: false,
            base_url: "http://127.0.0.1:8080/v1",
            model: "",
          }),
        } as Response);
      }
      if (url === "/provider-routing/llamacpp/status") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            configured: false,
            base_url: "http://127.0.0.1:8080/v1",
            model: "",
          }),
        } as Response);
      }
      if (url === "/provider-routing/nine-router/config" && method === "POST") {
        const ok = handlers.nineRouterConfigSaveOk ?? true;
        return Promise.resolve({
          ok,
          status: ok ? 200 : 400,
          json: async () =>
            ok
              ? {
                  configured: true,
                  base_url: "http://localhost:20128/v1",
                  masked: "9r-•••cdef",
                }
              : {
                  detail:
                    handlers.nineRouterConfigSaveDetail ?? "Config rejeitada",
                },
        } as Response);
      }
      if (
        url === "/provider-routing/nine-router/config" &&
        method === "DELETE"
      ) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            configured: false,
            base_url: null,
            masked: "",
          }),
        } as Response);
      }
      if (url === "/provider-routing/nine-router/models") {
        return Promise.resolve({
          ok: true,
          json: async () =>
            handlers.nineRouterDiscover ?? { reachable: false, models: [] },
        } as Response);
      }
      if (url === "/provider-routing/nine-router/registered" && !method) {
        return Promise.resolve({
          ok: true,
          json: async () => handlers.nineRouterRegistered ?? [],
        } as Response);
      }
      if (
        url === "/provider-routing/nine-router/registered" &&
        method === "POST"
      ) {
        const ok = handlers.nineRouterRegisterOk ?? true;
        return Promise.resolve({
          ok,
          status: ok ? 200 : 500,
          json: async () => ({
            id: "nr-new-id",
            tag: "cc/claude-opus-4-7",
            created_at: "now",
          }),
        } as Response);
      }
      if (
        typeof url === "string" &&
        url.startsWith("/provider-routing/nine-router/registered/")
      ) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ ok: true }),
        } as Response);
      }
      if (url === "/admin/media-models" && !method) {
        const body = handlers.mediaModelsGet;
        return Promise.resolve({
          ok: body !== null,
          status: body === null ? 500 : 200,
          json: async () => body ?? {},
        } as Response);
      }
      if (
        typeof url === "string" &&
        url.startsWith("/provider-routing/huggingface/models?q=")
      ) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ models: handlers.hfModels ?? [] }),
        } as Response);
      }
      if (
        typeof url === "string" &&
        url.startsWith("/provider-routing/huggingface/models/")
      ) {
        return Promise.resolve({
          ok: true,
          json: async () =>
            handlers.hfMetadata ?? { id: "owner/model", files: [] },
        } as Response);
      }
      if (url === "/provider-routing/llamacpp/runtime/status") {
        return Promise.resolve({
          ok: true,
          json: async () =>
            handlers.llamacppRuntimeStatus ?? {
              installed: false,
              path: null,
              files: [],
              runtimes: [],
            },
        } as Response);
      }
      if (
        typeof url === "string" &&
        url.startsWith("/provider-routing/llamacpp/runtime/") &&
        method === "DELETE"
      ) {
        return Promise.resolve({
          ok: handlers.llamacppRuntimeRemoveOk ?? true,
          status: handlers.llamacppRuntimeRemoveOk === false ? 409 : 200,
          json: async () => ({ ok: true }),
        } as Response);
      }
      if (url === "/admin/media-models" && method === "PATCH") {
        return Promise.resolve({
          ok: true,
          json: async () => handlers.mediaModelsPatch ?? { models: {} },
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        json: async () => ({}),
      } as Response);
    });
}

describe("ProviderRoutingTab - Hugging Face", () => {
  beforeEach(() => {
    overwriteGetLocale(() => "pt");
  });

  it("oferece uma �nica a��o de instala��o para cada modelo", async () => {
    mockFetch({
      registered: [],
      hfModels: [
        {
          id: "owner/model",
          name: "Modelo",
          format: "GGUF",
          compatibility: "prov�vel",
        },
      ],
      hfMetadata: {
        id: "owner/model",
        revision: "abc123",
        license: "mit",
        downloads: 12,
        files: [{ rfilename: "model.Q4_K_M.gguf", format: "GGUF" }],
      },
    });
    render(<ProviderRoutingTab />);
    fireEvent.click(await screen.findByRole("button", { name: /^buscar$/i }));
    expect(await screen.findByText("owner/model")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /baixar modelo/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /instalar modelo/i }));
    expect(await screen.findByText(/mit/i)).toBeTruthy();
  });
});

describe("ProviderRoutingTab — Ollama", () => {
  beforeEach(() => {
    overwriteGetLocale(() => "pt");
    mockFetch({ registered: [] });
  });

  it("mostra estado vazio quando nenhum modelo está registrado", async () => {
    render(<ProviderRoutingTab />);
    await waitFor(() => {
      // 3 seções: Ollama, OpenRouter, 9Router
      expect(screen.getAllByText(/nenhum modelo registrado/i).length).toBe(3);
    });
  });

  it("lista modelos já registrados", async () => {
    mockFetch({
      registered: [{ id: "1", tag: "qwen3:8b", created_at: "now" }],
    });
    render(<ProviderRoutingTab />);
    await waitFor(() => {
      expect(screen.getByText("qwen3:8b")).toBeTruthy();
    });
  });

  it("descoberta com host inacessível exibe aviso, sem crashar", async () => {
    mockFetch({ registered: [], discover: { reachable: false, models: [] } });
    render(<ProviderRoutingTab />);
    await waitFor(() => screen.getByRole("button", { name: /detectar/i }));
    fireEvent.click(screen.getByRole("button", { name: /detectar/i }));
    await waitFor(() => {
      expect(screen.getByText(/n[ãa]o consegui alcan[çc]ar/i)).toBeTruthy();
    });
  });

  it("descoberta com host acessível lista modelos e permite registrar", async () => {
    mockFetch({
      registered: [],
      discover: {
        reachable: true,
        models: [{ name: "qwen3:8b", size: 123, modified_at: null }],
      },
      registerOk: true,
    });
    render(<ProviderRoutingTab />);
    fireEvent.click(await screen.findByRole("button", { name: /detectar/i }));
    await waitFor(() => screen.getByText("qwen3:8b"));

    fireEvent.click(screen.getByRole("button", { name: /registrar/i }));
    await waitFor(() => {
      expect(screen.getAllByText("qwen3:8b").length).toBeGreaterThan(0);
    });
  });

  it("erro ao registrar exibe mensagem, não trava a UI", async () => {
    mockFetch({
      registered: [],
      discover: {
        reachable: true,
        models: [{ name: "qwen3:8b", size: 123, modified_at: null }],
      },
      registerOk: false,
    });
    render(<ProviderRoutingTab />);
    fireEvent.click(await screen.findByRole("button", { name: /detectar/i }));
    await waitFor(() => screen.getByText("qwen3:8b"));

    fireEvent.click(screen.getByRole("button", { name: /registrar/i }));
    await waitFor(() => {
      expect(screen.getByText(/erro ao registrar/i)).toBeTruthy();
    });
  });

  it("remove um modelo registrado", async () => {
    mockFetch({
      registered: [{ id: "1", tag: "qwen3:8b", created_at: "now" }],
      removeOk: true,
    });
    const { container } = render(<ProviderRoutingTab />);
    await waitFor(() => screen.getByText("qwen3:8b"));

    const removeBtn = container.querySelector(
      "button.hover\\:text-destructive",
    );
    expect(removeBtn).toBeTruthy();
    fireEvent.click(removeBtn!);

    await waitFor(() => {
      expect(screen.queryByText("qwen3:8b")).not.toBeInTheDocument();
    });
  });

  it("erro ao remover mantém o modelo na lista e mostra mensagem", async () => {
    mockFetch({
      registered: [{ id: "1", tag: "qwen3:8b", created_at: "now" }],
      removeOk: false,
    });
    const { container } = render(<ProviderRoutingTab />);
    await waitFor(() => screen.getByText("qwen3:8b"));

    const removeBtn = container.querySelector(
      "button.hover\\:text-destructive",
    );
    fireEvent.click(removeBtn!);

    await waitFor(() => {
      expect(screen.getByText(/erro ao remover/i)).toBeTruthy();
    });
    expect(screen.getByText("qwen3:8b")).toBeTruthy();
  });
});

describe("ProviderRoutingTab — OpenRouter", () => {
  beforeEach(() => {
    overwriteGetLocale(() => "pt");
  });

  it("mostra input de key quando não configurada", async () => {
    mockFetch({ openrouterStatus: { configured: false, masked: "" } });
    render(<ProviderRoutingTab />);
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/sk-or-v1/i)).toBeTruthy();
    });
  });

  it("salva key válida e mostra badge configurada", async () => {
    mockFetch({
      openrouterStatus: { configured: false, masked: "" },
      openrouterKeySaveOk: true,
    });
    render(<ProviderRoutingTab />);
    const input = await screen.findByPlaceholderText(/sk-or-v1/i);
    fireEvent.change(input, { target: { value: "sk-or-v1-abcdef" } });
    fireEvent.click(screen.getByRole("button", { name: /salvar/i }));

    await waitFor(() => {
      expect(screen.getByText(/configurada/i)).toBeTruthy();
      expect(screen.getByText("sk-or-•••cdef")).toBeTruthy();
    });
  });

  it("key rejeitada pela OpenRouter exibe mensagem, não salva", async () => {
    mockFetch({
      openrouterStatus: { configured: false, masked: "" },
      openrouterKeySaveOk: false,
      openrouterKeySaveDetail: "Key rejeitada pela OpenRouter (HTTP 401)",
    });
    render(<ProviderRoutingTab />);
    const input = await screen.findByPlaceholderText(/sk-or-v1/i);
    fireEvent.change(input, { target: { value: "bad-key" } });
    fireEvent.click(screen.getByRole("button", { name: /salvar/i }));

    await waitFor(() => {
      expect(screen.getByText(/key rejeitada/i)).toBeTruthy();
    });
    expect(screen.getByPlaceholderText(/sk-or-v1/i)).toBeTruthy();
  });

  it("com key configurada, busca no catálogo e registra um modelo", async () => {
    mockFetch({
      openrouterStatus: { configured: true, masked: "sk-or-•••cdef" },
      openrouterCatalog: [
        { id: "openai/gpt-4o", name: "GPT-4o", context_length: 128000 },
      ],
      openrouterRegisterOk: true,
    });
    render(<ProviderRoutingTab />);
    const search = await screen.findByPlaceholderText(/buscar modelo/i);
    fireEvent.change(search, { target: { value: "gpt-4o" } });

    await waitFor(
      () => {
        expect(screen.getByText("openai/gpt-4o")).toBeTruthy();
      },
      { timeout: 2000 },
    );

    const registerButtons = screen.getAllByRole("button", {
      name: /^registrar$/i,
    });
    fireEvent.click(registerButtons[0]);

    await waitFor(() => {
      expect(screen.getAllByText("openai/gpt-4o").length).toBeGreaterThan(0);
    });
  });

  it("remove a key configurada", async () => {
    mockFetch({
      openrouterStatus: { configured: true, masked: "sk-or-•••cdef" },
    });
    render(<ProviderRoutingTab />);
    await waitFor(() => screen.getByText("sk-or-•••cdef"));

    fireEvent.click(screen.getByRole("button", { name: /remover key/i }));

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/sk-or-v1/i)).toBeTruthy();
    });
  });
});

describe("ProviderRoutingTab — 9Router", () => {
  beforeEach(() => {
    overwriteGetLocale(() => "pt");
  });

  it("mostra inputs de endpoint e key quando não configurado", async () => {
    mockFetch({
      nineRouterStatus: { configured: false, base_url: null, masked: "" },
    });
    render(<ProviderRoutingTab />);
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/localhost:20128/i)).toBeTruthy();
    });
  });

  it("conecta com endpoint+key válidos e mostra badge configurado", async () => {
    mockFetch({
      nineRouterStatus: { configured: false, base_url: null, masked: "" },
      nineRouterConfigSaveOk: true,
    });
    render(<ProviderRoutingTab />);
    const urlInput = await screen.findByPlaceholderText(/localhost:20128/i);
    fireEvent.change(urlInput, {
      target: { value: "http://localhost:20128/v1" },
    });
    const keyInput = screen.getByPlaceholderText(/9router/i);
    fireEvent.change(keyInput, { target: { value: "9r-abcdef" } });
    fireEvent.click(screen.getByRole("button", { name: /conectar/i }));

    await waitFor(() => {
      expect(screen.getByText(/^configurado$/i)).toBeTruthy();
      expect(screen.getByText(/9r-•••cdef/)).toBeTruthy();
    });
  });

  it("configuração rejeitada exibe mensagem, não conecta", async () => {
    mockFetch({
      nineRouterStatus: { configured: false, base_url: null, masked: "" },
      nineRouterConfigSaveOk: false,
      nineRouterConfigSaveDetail: "base_url vazia",
    });
    render(<ProviderRoutingTab />);
    const urlInput = await screen.findByPlaceholderText(/localhost:20128/i);
    fireEvent.change(urlInput, {
      target: { value: "http://localhost:20128/v1" },
    });
    const keyInput = screen.getByPlaceholderText(/9router/i);
    fireEvent.change(keyInput, { target: { value: "9r-abcdef" } });
    fireEvent.click(screen.getByRole("button", { name: /conectar/i }));

    await waitFor(() => {
      expect(screen.getByText(/base_url vazia/i)).toBeTruthy();
    });
    expect(screen.getByPlaceholderText(/localhost:20128/i)).toBeTruthy();
  });

  it("com config configurada, detecta modelos e registra", async () => {
    mockFetch({
      nineRouterStatus: {
        configured: true,
        base_url: "http://localhost:20128/v1",
        masked: "9r-•••cdef",
      },
      nineRouterDiscover: {
        reachable: true,
        models: [{ id: "cc/claude-opus-4-7", name: "Claude Opus 4.7" }],
      },
      nineRouterRegisterOk: true,
    });
    render(<ProviderRoutingTab />);
    // Ollama também tem um botão "Detectar modelos" — escopa pela seção
    // 9Router (via o título) pra não clicar no botão errado.
    const nineRouterHeading = await screen.findByText(/^9router/i);
    const nineRouterSection = nineRouterHeading.closest("div.space-y-4")!;
    fireEvent.click(
      within(nineRouterSection as HTMLElement).getByRole("button", {
        name: /detectar/i,
      }),
    );
    await waitFor(() => screen.getByText("cc/claude-opus-4-7"));

    fireEvent.click(screen.getByRole("button", { name: /registrar/i }));
    await waitFor(() => {
      expect(screen.getAllByText("cc/claude-opus-4-7").length).toBeGreaterThan(
        0,
      );
    });
  });

  it("já configurado, detecta modelos automaticamente sem clicar no botão", async () => {
    // Regressão de UX: diferente do OpenRouter (busca ao digitar, sem botão),
    // o 9Router só listava modelos depois de clicar "Detectar modelos" — a
    // seção fica configurada e a lista deve aparecer sozinha, sem interação.
    mockFetch({
      nineRouterStatus: {
        configured: true,
        base_url: "http://localhost:20128/v1",
        masked: "9r-•••cdef",
      },
      nineRouterDiscover: {
        reachable: true,
        models: [{ id: "cc/claude-opus-4-7", name: "Claude Opus 4.7" }],
      },
    });
    render(<ProviderRoutingTab />);

    await waitFor(() => {
      expect(screen.getByText("cc/claude-opus-4-7")).toBeTruthy();
    });
  });

  it("proxy inacessível é detectado automaticamente, sem exigir clique", async () => {
    mockFetch({
      nineRouterStatus: {
        configured: true,
        base_url: "http://localhost:20128/v1",
        masked: "9r-•••cdef",
      },
      nineRouterDiscover: { reachable: false, models: [] },
    });
    render(<ProviderRoutingTab />);

    await waitFor(() => {
      expect(
        screen.getByText(/n[ãa]o consegui alcan[çc]ar o 9router/i),
      ).toBeTruthy();
    });
  });

  it("com mais de 5 modelos detectados, filtra a lista pela busca", async () => {
    const models = [
      "cc/claude-opus-4-7",
      "cc/claude-sonnet-5",
      "openai/gpt-5",
      "openai/gpt-5-mini",
      "google/gemini-3-pro",
      "google/gemini-3-flash",
    ].map((id) => ({ id, name: id }));
    mockFetch({
      nineRouterStatus: {
        configured: true,
        base_url: "http://localhost:20128/v1",
        masked: "9r-•••cdef",
      },
      nineRouterDiscover: { reachable: true, models },
    });
    render(<ProviderRoutingTab />);
    const nineRouterHeading = await screen.findByText(/^9router/i);
    const nineRouterSection = nineRouterHeading.closest("div.space-y-4")!;
    fireEvent.click(
      within(nineRouterSection as HTMLElement).getByRole("button", {
        name: /detectar/i,
      }),
    );
    await waitFor(() => screen.getByText("cc/claude-opus-4-7"));

    const searchInput = within(
      nineRouterSection as HTMLElement,
    ).getByPlaceholderText(/filtrar modelos/i);
    fireEvent.change(searchInput, { target: { value: "gemini" } });

    await waitFor(() => {
      expect(screen.getByText("google/gemini-3-pro")).toBeTruthy();
      expect(screen.getByText("google/gemini-3-flash")).toBeTruthy();
      expect(screen.queryByText("cc/claude-opus-4-7")).toBeNull();
      expect(screen.queryByText("openai/gpt-5")).toBeNull();
    });
  });

  it("busca sem nenhum resultado mostra mensagem, não lista vazia silenciosa", async () => {
    const models = [
      "cc/claude-opus-4-7",
      "cc/claude-sonnet-5",
      "openai/gpt-5",
      "openai/gpt-5-mini",
      "google/gemini-3-pro",
      "google/gemini-3-flash",
    ].map((id) => ({ id, name: id }));
    mockFetch({
      nineRouterStatus: {
        configured: true,
        base_url: "http://localhost:20128/v1",
        masked: "9r-•••cdef",
      },
      nineRouterDiscover: { reachable: true, models },
    });
    render(<ProviderRoutingTab />);
    const nineRouterHeading = await screen.findByText(/^9router/i);
    const nineRouterSection = nineRouterHeading.closest("div.space-y-4")!;
    fireEvent.click(
      within(nineRouterSection as HTMLElement).getByRole("button", {
        name: /detectar/i,
      }),
    );
    await waitFor(() => screen.getByText("cc/claude-opus-4-7"));

    const searchInput = within(
      nineRouterSection as HTMLElement,
    ).getByPlaceholderText(/filtrar modelos/i);
    fireEvent.change(searchInput, { target: { value: "modelo-inexistente" } });

    await waitFor(() => {
      expect(
        screen.getByText(/nenhum modelo detectado bate com a busca/i),
      ).toBeTruthy();
      expect(screen.queryByText("cc/claude-opus-4-7")).toBeNull();
    });
  });

  it("com poucos modelos (5 ou menos), não mostra a busca", async () => {
    mockFetch({
      nineRouterStatus: {
        configured: true,
        base_url: "http://localhost:20128/v1",
        masked: "9r-•••cdef",
      },
      nineRouterDiscover: {
        reachable: true,
        models: [{ id: "cc/claude-opus-4-7", name: "Claude Opus 4.7" }],
      },
    });
    render(<ProviderRoutingTab />);
    const nineRouterHeading = await screen.findByText(/^9router/i);
    const nineRouterSection = nineRouterHeading.closest("div.space-y-4")!;
    fireEvent.click(
      within(nineRouterSection as HTMLElement).getByRole("button", {
        name: /detectar/i,
      }),
    );
    await waitFor(() => screen.getByText("cc/claude-opus-4-7"));

    expect(
      within(nineRouterSection as HTMLElement).queryByPlaceholderText(
        /filtrar modelos/i,
      ),
    ).toBeNull();
  });

  it("proxy inacessível na descoberta exibe aviso, sem crashar", async () => {
    mockFetch({
      nineRouterStatus: {
        configured: true,
        base_url: "http://localhost:20128/v1",
        masked: "9r-•••cdef",
      },
      nineRouterDiscover: { reachable: false, models: [] },
    });
    render(<ProviderRoutingTab />);
    const nineRouterHeading = await screen.findByText(/^9router/i);
    const nineRouterSection = nineRouterHeading.closest("div.space-y-4")!;
    fireEvent.click(
      within(nineRouterSection as HTMLElement).getByRole("button", {
        name: /detectar/i,
      }),
    );
    await waitFor(() => {
      expect(
        screen.getByText(/n[ãa]o consegui alcan[çc]ar o 9router/i),
      ).toBeTruthy();
    });
  });

  it("desconecta a configuração", async () => {
    mockFetch({
      nineRouterStatus: {
        configured: true,
        base_url: "http://localhost:20128/v1",
        masked: "9r-•••cdef",
      },
    });
    render(<ProviderRoutingTab />);
    await waitFor(() => screen.getByText(/9r-•••cdef/));

    fireEvent.click(screen.getByRole("button", { name: /desconectar/i }));

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/localhost:20128/i)).toBeTruthy();
    });
  });
});

describe("MediaModelsSection", () => {
  // Config por gateway (Ollama/OpenRouter), não preferência genérica de
  // usuário — ver provider-routing-tab.tsx para o racional completo.
  it("repinta com o valor efetivo devolvido pelo backend ao limpar o campo", async () => {
    // Cenário do invariante: o usuário limpa o campo, mas existe env var —
    // o que passa a valer é a env. Mostrar o campo vazio faria parecer que a
    // capacidade ficou desligada.
    mockFetch({
      mediaModelsGet: { models: { ollama_image_model: "escolha-da-ui" } },
      mediaModelsPatch: { models: { ollama_image_model: "modelo-da-env" } },
    });

    render(<ProviderRoutingTab />);

    const campo = (await screen.findByLabelText(
      "Ollama — image",
    )) as HTMLInputElement;
    expect(campo.value).toBe("escolha-da-ui");

    fireEvent.change(campo, { target: { value: "" } });
    fireEvent.blur(campo);

    // Erro/borda: o campo NÃO fica vazio — mostra o que de fato vale agora.
    await waitFor(() => expect(campo.value).toBe("modelo-da-env"));
  });

  it("backend fora do ar deixa os campos vazios sem quebrar a aba (edge)", async () => {
    mockFetch({ mediaModelsGet: null });

    render(<ProviderRoutingTab />);

    const campo = (await screen.findByLabelText(
      "Ollama — voice",
    )) as HTMLInputElement;
    expect(campo.value).toBe("");
  });
});

describe("LlamaCppSection", () => {
  beforeEach(() => {
    overwriteGetLocale(() => "pt");
  });

  it("remove uma versão inativa sem permitir remover a ativa", async () => {
    const runtimeStatus = {
      installed: true,
      path: "/tmp/llama-server",
      files: ["llama-server"],
      active_runtime: "active-runtime",
      runtimes: [
        { id: "active-runtime", asset: "llama-active" },
        { id: "old-runtime", asset: "llama-old" },
      ],
    };
    mockFetch({ llamacppRuntimeStatus: runtimeStatus });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<ProviderRoutingTab />);

    const removeButtons = await screen.findAllByRole("button", {
      name: /remover runtime gerenciado/i,
    });
    expect(removeButtons.length).toBe(2);
    fireEvent.click(removeButtons[0]);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/provider-routing/llamacpp/runtime/old-runtime",
        { method: "DELETE" },
      );
    });
  });
});

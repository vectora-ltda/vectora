// @vitest-environment jsdom
/**
 * RagSettingsPanel: o gear abre o painel, carrega /rag/settings + /rag/collections,
 * faz PATCH ao alternar o reranker, e lista coleções com botão de apagar.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  cleanup,
  fireEvent,
  waitFor,
} from "@testing-library/react";

import { RagSettingsPanel } from "../rag-settings-panel";

const FETCH = vi.fn();

function jsonRes(body: unknown, ok = true) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status: ok ? 200 : 500,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

beforeEach(() => {
  FETCH.mockReset();
  vi.stubGlobal("fetch", FETCH);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("RagSettingsPanel", () => {
  it("o gear abre o painel e carrega settings + coleções", async () => {
    FETCH.mockImplementation((url: string) => {
      if (url.includes("/rag/settings"))
        return jsonRes({
          reranker_enabled: true,
          reranker_top_k: 7,
          rerank_provider: "auto",
          embed_provider: "auto",
          ingest_file_types: [],
        });
      if (url.includes("/rag/collections"))
        return jsonRes({ collections: [{ name: "articles", count: 3 }] });
      return jsonRes({});
    });

    render(<RagSettingsPanel />);
    expect(screen.queryByTestId("rag-settings-panel")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("rag-settings-btn"));

    await waitFor(() =>
      expect(screen.getByTestId("rag-settings-panel")).toBeInTheDocument(),
    );
    // top_k carregado do backend
    await waitFor(() =>
      expect((screen.getByDisplayValue("7") as HTMLInputElement).value).toBe(
        "7",
      ),
    );
    // coleção listada
    expect(screen.getByText("articles")).toBeInTheDocument();
  });

  it("alternar o reranker dispara PATCH /rag/settings", async () => {
    FETCH.mockImplementation((url: string) => {
      if (url.includes("/rag/settings"))
        return jsonRes({
          reranker_enabled: true,
          reranker_top_k: 5,
          rerank_provider: "auto",
          embed_provider: "auto",
          ingest_file_types: [],
        });
      if (url.includes("/rag/collections")) return jsonRes({ collections: [] });
      return jsonRes({});
    });

    render(<RagSettingsPanel />);
    fireEvent.click(screen.getByTestId("rag-settings-btn"));
    await waitFor(() =>
      expect(screen.getByTestId("rag-settings-panel")).toBeInTheDocument(),
    );

    const checkbox = screen.getAllByRole("checkbox")[0] as HTMLInputElement;
    fireEvent.click(checkbox);

    await waitFor(() => {
      const patchCall = FETCH.mock.calls.find(
        (c) => c[0].includes("/rag/settings") && c[1]?.method === "PATCH",
      );
      expect(patchCall).toBeTruthy();
      expect(JSON.parse(patchCall![1].body as string)).toHaveProperty(
        "reranker_enabled",
        false,
      );
    });
  });

  it("escolher Ollama como embed_provider descobre modelos e persiste a escolha", async () => {
    // Simula o backend real: PATCH mescla no estado e devolve o resultado —
    // sem isso, um mock "burro" (mesma resposta canônica pra GET e PATCH)
    // reverte o update otimista de volta pra embed_provider="auto" assim
    // que o PATCH resolve, desmontando o EmbedModelPicker antes do teste
    // conseguir observar o dropdown de modelos.
    let ragState = {
      reranker_enabled: true,
      reranker_top_k: 5,
      rerank_provider: "auto",
      embed_provider: "auto",
      embed_model: "",
      ingest_file_types: [] as string[],
    };
    FETCH.mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/provider-routing/ollama/models"))
        return jsonRes({
          reachable: true,
          models: [
            { name: "qwen3-embedding:0.6b", size: null, modified_at: null },
          ],
        });
      if (url.includes("/rag/settings")) {
        if (init?.method === "PATCH") {
          ragState = { ...ragState, ...JSON.parse(init.body as string) };
        }
        return jsonRes(ragState);
      }
      if (url.includes("/rag/collections")) return jsonRes({ collections: [] });
      return jsonRes({});
    });

    render(<RagSettingsPanel />);
    fireEvent.click(screen.getByTestId("rag-settings-btn"));
    await waitFor(() =>
      expect(screen.getByTestId("rag-settings-panel")).toBeInTheDocument(),
    );

    // segundo <select> é o de embed_provider (o primeiro é rerank_provider)
    const embedProviderSelect = screen.getAllByRole("combobox")[1];
    fireEvent.change(embedProviderSelect, { target: { value: "ollama" } });

    await waitFor(() =>
      expect(
        FETCH.mock.calls.some((c) =>
          c[0].includes("/provider-routing/ollama/models"),
        ),
      ).toBe(true),
    );
    await waitFor(() =>
      expect(screen.getByText("qwen3-embedding:0.6b")).toBeInTheDocument(),
    );

    const modelSelect = screen.getAllByRole("combobox")[2];
    fireEvent.change(modelSelect, {
      target: { value: "qwen3-embedding:0.6b" },
    });

    await waitFor(() => {
      // .findLast: seleção do provider e do modelo disparam 2 PATCHes
      // separados (patch() só envia o campo que mudou) — o do modelo é o
      // último, não o primeiro.
      const patchCall = FETCH.mock.calls.findLast(
        (c) => c[0].includes("/rag/settings") && c[1]?.method === "PATCH",
      );
      expect(patchCall).toBeTruthy();
      expect(JSON.parse(patchCall![1].body as string)).toHaveProperty(
        "embed_model",
        "qwen3-embedding:0.6b",
      );
    });
  });

  it("par de erro: Ollama fora do ar não quebra o painel, lista fica vazia", async () => {
    let ragState = {
      reranker_enabled: true,
      reranker_top_k: 5,
      rerank_provider: "auto",
      embed_provider: "auto",
      embed_model: "",
      ingest_file_types: [] as string[],
    };
    FETCH.mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/provider-routing/ollama/models"))
        return Promise.reject(new Error("fetch failed"));
      if (url.includes("/rag/settings")) {
        if (init?.method === "PATCH") {
          ragState = { ...ragState, ...JSON.parse(init.body as string) };
        }
        return jsonRes(ragState);
      }
      if (url.includes("/rag/collections")) return jsonRes({ collections: [] });
      return jsonRes({});
    });

    render(<RagSettingsPanel />);
    fireEvent.click(screen.getByTestId("rag-settings-btn"));
    await waitFor(() =>
      expect(screen.getByTestId("rag-settings-panel")).toBeInTheDocument(),
    );

    const embedProviderSelect = screen.getAllByRole("combobox")[1];
    fireEvent.change(embedProviderSelect, { target: { value: "ollama" } });

    await waitFor(() =>
      expect(
        FETCH.mock.calls.some((c) =>
          c[0].includes("/provider-routing/ollama/models"),
        ),
      ).toBe(true),
    );
    // painel continua de pé, seletor de modelo só com o placeholder
    const modelSelect = screen.getAllByRole("combobox")[2] as HTMLSelectElement;
    expect(modelSelect.options.length).toBe(1);
  });

  it("provider de rerank sem chave aparece desabilitado no dropdown", async () => {
    FETCH.mockImplementation((url: string) => {
      if (url.includes("/rag/settings"))
        return jsonRes({
          reranker_enabled: true,
          reranker_top_k: 5,
          rerank_provider: "auto",
          embed_provider: "auto",
          ingest_file_types: [],
          rerank_provider_available: {
            cohere: false,
            voyage: false,
            openrouter: false,
          },
        });
      if (url.includes("/rag/collections")) return jsonRes({ collections: [] });
      return jsonRes({});
    });

    render(<RagSettingsPanel />);
    fireEvent.click(screen.getByTestId("rag-settings-btn"));
    await waitFor(() =>
      expect(screen.getByTestId("rag-settings-panel")).toBeInTheDocument(),
    );

    const rerankSelect = screen.getAllByRole(
      "combobox",
    )[0] as HTMLSelectElement;
    const cohereOption = [...rerankSelect.options].find(
      (o) => o.value === "cohere",
    );
    const openrouterOption = [...rerankSelect.options].find(
      (o) => o.value === "openrouter",
    );

    expect(cohereOption?.disabled).toBe(true);
    expect(cohereOption?.text).toContain("no key");
    expect(openrouterOption?.disabled).toBe(true);
  });

  it("selecionar um provider indisponível mostra o aviso inline", async () => {
    FETCH.mockImplementation((url: string) => {
      if (url.includes("/rag/settings"))
        return jsonRes({
          reranker_enabled: true,
          reranker_top_k: 5,
          rerank_provider: "cohere",
          embed_provider: "auto",
          ingest_file_types: [],
          rerank_provider_available: { cohere: false, voyage: true },
        });
      if (url.includes("/rag/collections")) return jsonRes({ collections: [] });
      return jsonRes({});
    });

    render(<RagSettingsPanel />);
    fireEvent.click(screen.getByTestId("rag-settings-btn"));
    await waitFor(() =>
      expect(screen.getByTestId("rag-settings-panel")).toBeInTheDocument(),
    );

    expect(screen.getByText(/reranking is off/i)).toBeInTheDocument();
  });

  it("provider 'auto' nunca mostra o aviso, mesmo com providers indisponíveis (edge)", async () => {
    FETCH.mockImplementation((url: string) => {
      if (url.includes("/rag/settings"))
        return jsonRes({
          reranker_enabled: true,
          reranker_top_k: 5,
          rerank_provider: "auto",
          embed_provider: "auto",
          ingest_file_types: [],
          rerank_provider_available: {
            cohere: false,
            voyage: false,
            openrouter: false,
          },
        });
      if (url.includes("/rag/collections")) return jsonRes({ collections: [] });
      return jsonRes({});
    });

    render(<RagSettingsPanel />);
    fireEvent.click(screen.getByTestId("rag-settings-btn"));
    await waitFor(() =>
      expect(screen.getByTestId("rag-settings-panel")).toBeInTheDocument(),
    );

    expect(screen.queryByText(/reranking is off/i)).not.toBeInTheDocument();
  });
});

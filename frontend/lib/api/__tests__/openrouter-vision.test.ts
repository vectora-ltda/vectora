import { describe, it, expect, vi, afterEach } from "vitest";
import { checkOpenRouterModelSupportsImage } from "../openrouter-vision";

// O módulo mantém um cache em memória por model id (`Map` no escopo do
// módulo, sem reset entre testes) — cada teste usa um id de modelo
// próprio pra não ler resultado cacheado por outro caso.
describe("checkOpenRouterModelSupportsImage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("retorna true quando o modelo tem 'image' em input_modalities", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          models: [
            { id: "openai/gpt-4o", input_modalities: ["text", "image"] },
          ],
        }),
      }),
    );
    expect(await checkOpenRouterModelSupportsImage("openai/gpt-4o")).toBe(true);
  });

  it("retorna false quando o modelo existe no catálogo mas sem 'image'", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          models: [{ id: "meta/llama-text-only", input_modalities: ["text"] }],
        }),
      }),
    );
    expect(
      await checkOpenRouterModelSupportsImage("meta/llama-text-only"),
    ).toBe(false);
  });

  it("falha aberto (true) quando o modelo não aparece no catálogo retornado", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ models: [] }),
      }),
    );
    expect(await checkOpenRouterModelSupportsImage("modelo/inexistente")).toBe(
      true,
    );
  });

  it("falha aberto (true) quando a resposta HTTP não é ok", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    expect(await checkOpenRouterModelSupportsImage("qualquer/modelo")).toBe(
      true,
    );
  });

  it("falha aberto (true) em erro de rede (fetch rejeita)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    expect(await checkOpenRouterModelSupportsImage("qualquer/modelo")).toBe(
      true,
    );
  });

  it("consulta a URL com o id do modelo codificado como querystring", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ models: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    await checkOpenRouterModelSupportsImage("anthropic/claude-3.5-sonnet");
    expect(fetchMock).toHaveBeenCalledWith(
      "/provider-routing/openrouter/models?q=anthropic%2Fclaude-3.5-sonnet",
    );
  });

  it("reusa o cache em memória — segunda chamada pro mesmo modelo não refaz fetch", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        models: [{ id: "cache/model", input_modalities: ["image"] }],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await checkOpenRouterModelSupportsImage("cache/model");
    await checkOpenRouterModelSupportsImage("cache/model");

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

import { describe, it, expect, vi, afterEach } from "vitest";
import { fetchPrefs, pushPrefs } from "../settings-prefs";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchPrefs", () => {
  it("devolve o JSON quando a resposta é ok", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          theme: "dark",
          selectedModel: "cohere:command-a",
        }),
      }),
    );
    const prefs = await fetchPrefs();
    expect(prefs).toEqual({ theme: "dark", selectedModel: "cohere:command-a" });
  });

  it("devolve objeto vazio quando a resposta não é ok", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    expect(await fetchPrefs()).toEqual({});
  });

  it("devolve objeto vazio em falha de rede (sem lançar)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    await expect(fetchPrefs()).resolves.toEqual({});
  });
});

describe("pushPrefs", () => {
  it("faz PATCH com o corpo serializado", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);
    await expect(pushPrefs({ theme: "dark" })).resolves.toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/settings/prefs",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ theme: "dark" }),
      }),
    );
  });

  it("retorna false em falha de rede para permitir rollback otimista", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    await expect(pushPrefs({ theme: "dark" })).resolves.toBe(false);
  });

  it("retorna false quando o backend rejeita a atualização", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 503 }),
    );
    await expect(pushPrefs({ theme: "dark" })).resolves.toBe(false);
  });
});

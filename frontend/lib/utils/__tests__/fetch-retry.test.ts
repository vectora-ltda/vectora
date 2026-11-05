/**
 * fetchJsonWithRetry / withRetry — política de retry.
 *
 * O ponto crítico não é "retenta", é NÃO retentar: 4xx e abort repetidos
 * multiplicam carga sem chance de sucesso, e retry em operação não
 * idempotente duplicaria efeito colateral.
 */

import { describe, expect, it, vi, afterEach } from "vitest";
import { fetchJsonWithRetry, withRetry, FetchHttpError } from "../fetch-retry";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe("fetchJsonWithRetry", () => {
  it("devolve o JSON na primeira tentativa quando a resposta é OK", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: 1 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchJsonWithRetry("/x")).resolves.toEqual({ ok: 1 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("retenta falha de rede e devolve o resultado da tentativa bem-sucedida", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue(jsonResponse({ ok: 2 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchJsonWithRetry("/x", undefined, { backoffMs: 1 }),
    ).resolves.toEqual({ ok: 2 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("retenta 5xx até o teto e então propaga o erro com o status preservado", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}, 503));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchJsonWithRetry("/x", undefined, { retries: 2, backoffMs: 1 }),
    ).rejects.toMatchObject({ name: "FetchHttpError", status: 503 });
    // 1 tentativa + 2 retries.
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("erro/borda: 4xx NUNCA é retentado — repetir não muda o resultado", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}, 404));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchJsonWithRetry("/x", undefined, { retries: 5, backoffMs: 1 }),
    ).rejects.toBeInstanceOf(FetchHttpError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("erro/borda: AbortError não é retentado (cancelamento é intencional)", async () => {
    const abort = Object.assign(new Error("Aborted"), { name: "AbortError" });
    const fetchMock = vi.fn().mockRejectedValue(abort);
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchJsonWithRetry("/x", undefined, { retries: 5, backoffMs: 1 }),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("erro/borda: retries=0 falha na primeira tentativa, sem nenhuma repetição", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}, 500));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchJsonWithRetry("/x", undefined, { retries: 0, backoffMs: 1 }),
    ).rejects.toMatchObject({ status: 500 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("withRetry", () => {
  it("retenta erro genérico e devolve o valor da tentativa bem-sucedida", async () => {
    const fn = vi
      .fn()
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValue("pronto");

    await expect(withRetry(fn, { backoffMs: 1 })).resolves.toBe("pronto");
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("erro/borda: 4xx detectado no texto do erro não é retentado", async () => {
    // Formato de postRpc: "<path> failed (<status>): <body>".
    const fn = vi
      .fn()
      .mockRejectedValue(new Error("/threads failed (403): forbidden"));

    await expect(withRetry(fn, { retries: 5, backoffMs: 1 })).rejects.toThrow(
      /403/,
    );
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("5xx no texto do erro CONTINUA sendo retentado (só 4xx é terminal)", async () => {
    const fn = vi
      .fn()
      .mockRejectedValue(new Error("/threads failed (500): boom"));

    await expect(withRetry(fn, { retries: 2, backoffMs: 1 })).rejects.toThrow(
      /500/,
    );
    expect(fn).toHaveBeenCalledTimes(3);
  });
});

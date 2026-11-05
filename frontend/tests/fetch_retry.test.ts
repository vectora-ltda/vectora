/**
 * Tests para withRetry: backoff em erros transitórios, sem retry em 4xx/abort.
 */

import { describe, expect, it, vi } from "vitest";
import { withRetry } from "@/lib/utils/fetch-retry";

describe("withRetry", () => {
  it("retorna o valor na primeira tentativa bem-sucedida", async () => {
    const fn = vi.fn().mockResolvedValue("ok");
    await expect(withRetry(fn, { backoffMs: 0 })).resolves.toBe("ok");
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("re-tenta erros transitórios e eventualmente sucede", async () => {
    const fn = vi
      .fn()
      .mockRejectedValueOnce(new Error("network error"))
      .mockResolvedValue("ok");
    await expect(withRetry(fn, { retries: 2, backoffMs: 0 })).resolves.toBe(
      "ok",
    );
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("propaga o erro após esgotar as tentativas", async () => {
    const fn = vi.fn().mockRejectedValue(new Error("boom"));
    await expect(withRetry(fn, { retries: 2, backoffMs: 0 })).rejects.toThrow(
      "boom",
    );
    expect(fn).toHaveBeenCalledTimes(3); // 1 + 2 retries
  });

  it("não re-tenta erro 4xx (status na mensagem, formato (404))", async () => {
    const fn = vi
      .fn()
      .mockRejectedValue(new Error("/api/x failed (404): not found"));
    await expect(withRetry(fn, { retries: 3, backoffMs: 0 })).rejects.toThrow(
      "(404)",
    );
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("não re-tenta quando o erro é AbortError", async () => {
    const abortErr = new DOMException("Aborted", "AbortError");
    const fn = vi.fn().mockRejectedValue(abortErr);
    await expect(withRetry(fn, { retries: 3, backoffMs: 0 })).rejects.toBe(
      abortErr,
    );
    expect(fn).toHaveBeenCalledTimes(1);
  });
});

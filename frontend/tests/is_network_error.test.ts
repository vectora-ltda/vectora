/**
 * Tests para isNetworkError: distingue falhas de transporte (rede caiu,
 * DNS, socket) de erros de aplicação reportados pelo backend.
 */

import { describe, expect, it } from "vitest";
import { isNetworkError } from "@/lib/hooks/chat/use-stream-handler";

describe("isNetworkError", () => {
  it("TypeError é sempre falha de rede (fetch lança TypeError)", () => {
    expect(isNetworkError(new TypeError("Failed to fetch"))).toBe(true);
  });

  it.each([
    "Failed to fetch",
    "NetworkError when attempting to fetch resource",
    "network error",
    "Load failed",
    "read ECONNRESET",
    "connect ECONNREFUSED 127.0.0.1:8080",
  ])("reconhece mensagem de rede: %s", (msg) => {
    expect(isNetworkError(new Error(msg))).toBe(true);
  });

  it("erro de aplicação não é de rede", () => {
    expect(isNetworkError(new Error("400 Bad Request"))).toBe(false);
    expect(isNetworkError(new Error("rate limit exceeded"))).toBe(false);
  });

  it("aceita valores não-Error (string crua)", () => {
    expect(isNetworkError("Failed to fetch")).toBe(true);
    expect(isNetworkError("algo qualquer")).toBe(false);
  });
});

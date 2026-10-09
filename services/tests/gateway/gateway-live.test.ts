/**
 * Contrato live do gateway Cloudflare.
 *
 * Este teste nunca usa mocks: só roda quando explicitamente habilitado e
 * consulta o Worker implantado com um token de health fornecido pelo
 * operador. Assim, falhas de rota, secret do Durable Object e Content-Type
 * incorreto aparecem no CI/manual sem tornar a suíte local dependente da
 * infraestrutura externa.
 */
import { describe, expect, it } from "vitest";

const liveEnabled =
  process.env.VECTORA_LIVE_SERVICES === "1" &&
  Boolean(process.env.GATEWAY_HEALTH_TOKEN);

describe.skipIf(!liveEnabled)("gateway Cloudflare — contrato live", () => {
  it("retorna health JSON pelo endpoint público", async () => {
    const baseUrl =
      process.env.GATEWAY_URL?.replace(/\/$/, "") ??
      "https://gateway.vectora.chat";
    const token = process.env.GATEWAY_HEALTH_TOKEN;
    if (!token) throw new Error("GATEWAY_HEALTH_TOKEN não configurado");

    const response = await fetch(`${baseUrl}/health/${token}`);
    const contentType = response.headers.get("content-type") ?? "";
    const body = await response.text();

    expect(response.status, body).toBe(200);
    expect(contentType).toMatch(/^application\/json(?:;|$)/);

    const payload: unknown = JSON.parse(body);
    expect(payload).toEqual(
      expect.objectContaining({
        connected: expect.any(Boolean),
        queued: expect.any(Number),
      }),
    );
  });
});

import { env } from "cloudflare:test";
import { describe, expect, it, vi, afterEach } from "vitest";
import { oauth } from "../../src/oauth/routes";
import { createSession } from "../../src/auth/session";

afterEach(() => {
  vi.unstubAllGlobals();
});

async function makeUserWithSession(withToken: boolean) {
  const userId = crypto.randomUUID();
  await env.DB.prepare(
    "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
  )
    .bind(userId, `${userId}@example.com`, "pbkdf2$1$AA==$AA==")
    .run();
  await env.DB.prepare(
    "INSERT INTO tokens (id, user_id, token, token_hash) VALUES (?, ?, ?, ?)",
  )
    .bind(crypto.randomUUID(), userId, withToken ? "raw-token" : null, "hash")
    .run();
  const session = await createSession(env.DB, userId);
  return session.token;
}

describe("POST /oauth/device", () => {
  it("rejects unauthenticated requests and requests missing state", async () => {
    expect(
      (
        await oauth.request(
          "/device",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: "{}",
          },
          env,
        )
      ).status,
    ).toBe(401);

    const token = await makeUserWithSession(true);
    const missingState = await oauth.request(
      "/device",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: "{}",
      },
      env,
    );
    expect(missingState.status).toBe(400);
  });

  it("returns no_token when the show-once token was already revealed", async () => {
    const token = await makeUserWithSession(false);
    const res = await oauth.request(
      "/device",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ state: "abc" }),
      },
      env,
    );
    expect(res.status).toBe(409);
    expect(await res.json()).toEqual({ error: "no_token" });
  });

  it("forwards the token to gateway/oauth/token and returns ok on success", async () => {
    const token = await makeUserWithSession(true);
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      expect(url).toBe("https://gateway.vectora.chat/oauth/token");
      expect(init.headers).toMatchObject({
        Authorization: "Bearer test-oauth-secret",
      });
      return new Response(JSON.stringify({ ok: true }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const res = await oauth.request(
      "/device",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ state: "abc" }),
      },
      env,
    );
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
  });

  it("returns a 502 when the gateway call fails", async () => {
    const token = await makeUserWithSession(true);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 500 })),
    );

    const res = await oauth.request(
      "/device",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ state: "abc" }),
      },
      env,
    );
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ error: "gateway_error" });
  });
});

describe("OAuth broker de integrações", () => {
  function makeOAuthResultTestEnv() {
    const values = new Map<string, string>();
    const binding = {
      idFromName: (name: string) => name,
      get: (id: string) => ({
        fetch: async (input: string, init?: RequestInit) => {
          const url = new URL(input);
          const key = `${id}:${url.searchParams.get("key") ?? ""}`;
          if (
            init?.method === "POST" &&
            url.searchParams.get("action") === "claim"
          ) {
            const value = values.get(key);
            if (value === undefined) return new Response(null, { status: 202 });
            const provider = url.searchParams.get("provider");
            if (provider && JSON.parse(value).provider !== provider)
              return Response.json(
                { error: "provider_mismatch" },
                { status: 409 },
              );
            values.delete(key);
            return new Response(value, { status: 200 });
          }
          if (init?.method === "POST") {
            const payload = JSON.parse(String(init.body)) as { value: string };
            values.set(key, payload.value);
            return new Response(null, { status: 204 });
          }
          if (init?.method === "DELETE") {
            values.delete(key);
            return new Response(null, { status: 204 });
          }
          const value = values.get(key);
          if (value === undefined) return new Response(null, { status: 202 });
          if (url.searchParams.get("consume") !== "false") values.delete(key);
          return new Response(JSON.stringify(JSON.parse(value)), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        },
      }),
    };
    return { ...env, OAUTH_RESULT: binding } as unknown as typeof env;
  }

  async function startGithub(state: string, runtimeEnv: typeof env) {
    const response = await oauth.request(
      `/integrations/github/start?state=${state}&return_to=https%3A%2F%2Fabc.vectora.chat%2Fauth%2Fgithub%2Fcallback`,
      {},
      runtimeEnv,
    );
    expect(response.status).toBe(302);
  }

  it("rejeita state curto e redirect fora de vectora.chat", async () => {
    const originalId = (env as unknown as Record<string, string | undefined>)
      .GITHUB_OAUTH_CLIENT_ID;
    const originalSecret = (
      env as unknown as Record<string, string | undefined>
    ).GITHUB_OAUTH_CLIENT_SECRET;
    const runtimeEnv = env as unknown as Record<string, string | undefined>;
    runtimeEnv.GITHUB_OAUTH_CLIENT_ID = "company-client";
    runtimeEnv.GITHUB_OAUTH_CLIENT_SECRET = "company-secret";
    try {
      const short = await oauth.request(
        "/integrations/github/start?state=short&return_to=https%3A%2F%2Fabc.vectora.chat%2Fauth%2Fgithub%2Fcallback",
        {},
        env,
      );
      expect(short.status).toBe(400);
      const invalidRedirect = await oauth.request(
        `/integrations/github/start?state=${"a".repeat(32)}&return_to=https%3A%2F%2Fevil.example%2Fcallback`,
        {},
        env,
      );
      expect(invalidRedirect.status).toBe(400);
    } finally {
      runtimeEnv.GITHUB_OAUTH_CLIENT_ID = originalId;
      runtimeEnv.GITHUB_OAUTH_CLIENT_SECRET = originalSecret;
    }
  });

  it("exige o segredo de aplicação no polling do resultado", async () => {
    const state = "a".repeat(32);
    const response = await oauth.request(
      `/integrations/github/result/${state}`,
      {},
      env,
    );
    expect(response.status).toBe(401);
  });

  it("finaliza negações, tokens ausentes e falhas de troca como resultados terminais", async () => {
    const runtime = makeOAuthResultTestEnv();
    const runtimeEnv = runtime as unknown as Record<string, string | undefined>;
    const originalId = runtimeEnv.GITHUB_OAUTH_CLIENT_ID;
    const originalSecret = runtimeEnv.GITHUB_OAUTH_CLIENT_SECRET;
    runtimeEnv.GITHUB_OAUTH_CLIENT_ID = "company-client";
    runtimeEnv.GITHUB_OAUTH_CLIENT_SECRET = "company-secret";
    try {
      const deniedState = "d".repeat(32);
      await startGithub(deniedState, runtime);
      const denied = await oauth.request(
        `/integrations/github/callback?state=${deniedState}&error=access_denied`,
        {},
        runtime,
      );
      expect(denied.status).toBe(302);
      expect(denied.headers.get("set-cookie")).toContain(
        "vectora_oauth_transaction=",
      );
      expect(denied.headers.get("set-cookie")).toContain(
        "Domain=.vectora.chat",
      );
      const deniedResult = await oauth.request(
        `/integrations/github/result/${deniedState}`,
        { headers: { Authorization: "Bearer test-oauth-secret" } },
        runtime,
      );
      expect(await deniedResult.json()).toMatchObject({
        provider: "github",
        error: "access_denied",
      });

      const missingState = "m".repeat(32);
      await startGithub(missingState, runtime);
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => new Response(JSON.stringify({ ok: true }))),
      );
      const missing = await oauth.request(
        `/integrations/github/callback?state=${missingState}&code=missing`,
        {},
        runtime,
      );
      expect(missing.status).toBe(302);
      const missingResult = await oauth.request(
        `/integrations/github/result/${missingState}`,
        { headers: { Authorization: "Bearer test-oauth-secret" } },
        runtime,
      );
      expect(await missingResult.json()).toMatchObject({
        error: "token_missing",
      });

      const networkState = "n".repeat(32);
      await startGithub(networkState, runtime);
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => {
          throw new Error("network down");
        }),
      );
      const network = await oauth.request(
        `/integrations/github/callback?state=${networkState}&code=network`,
        {},
        runtime,
      );
      expect(network.status).toBe(302);
      const networkResult = await oauth.request(
        `/integrations/github/result/${networkState}`,
        { headers: { Authorization: "Bearer test-oauth-secret" } },
        runtime,
      );
      expect(await networkResult.json()).toMatchObject({
        error: "token_exchange_failed",
      });
    } finally {
      runtimeEnv.GITHUB_OAUTH_CLIENT_ID = originalId;
      runtimeEnv.GITHUB_OAUTH_CLIENT_SECRET = originalSecret;
    }
  });

  it("reivindica o estado antes da troca e rejeita callbacks concorrentes", async () => {
    const runtime = makeOAuthResultTestEnv();
    const runtimeEnv = runtime as unknown as Record<string, string | undefined>;
    const originalId = runtimeEnv.GITHUB_OAUTH_CLIENT_ID;
    const originalSecret = runtimeEnv.GITHUB_OAUTH_CLIENT_SECRET;
    runtimeEnv.GITHUB_OAUTH_CLIENT_ID = "company-client";
    runtimeEnv.GITHUB_OAUTH_CLIENT_SECRET = "company-secret";
    try {
      const state = "c".repeat(32);
      await startGithub(state, runtime);
      vi.stubGlobal(
        "fetch",
        vi.fn(
          async () =>
            new Response(JSON.stringify({ access_token: "winner" }), {
              headers: { "Content-Type": "application/json" },
            }),
        ),
      );
      const responses = await Promise.all([
        oauth.request(
          `/integrations/github/callback?state=${state}&code=one`,
          {},
          runtime,
        ),
        oauth.request(
          `/integrations/github/callback?state=${state}&code=two`,
          {},
          runtime,
        ),
      ]);
      expect(responses.map((response) => response.status).sort()).toEqual([
        302, 400,
      ]);
      const result = await oauth.request(
        `/integrations/github/result/${state}`,
        { headers: { Authorization: "Bearer test-oauth-secret" } },
        runtime,
      );
      expect(await result.json()).toMatchObject({
        provider: "github",
        accessToken: "winner",
      });
    } finally {
      runtimeEnv.GITHUB_OAUTH_CLIENT_ID = originalId;
      runtimeEnv.GITHUB_OAUTH_CLIENT_SECRET = originalSecret;
    }
  });
});

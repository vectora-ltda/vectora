import { env, SELF } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";
import { ghaBot } from "../../src/gha-bot/routes";
import { createSession, sha256Hex } from "../../src/auth/session";
import { hashConnectorSecret } from "../../src/gateway/auth";

// Testes que criam Durable Object (SQLite no disco) — mesma limitação de
// gateway/gateway-session.test.ts: workerd não libera o lock do arquivo
// no Windows antes do cleanup do isolated storage. Passam em CI (Linux).
const itDO = env.TEST_IS_WINDOWS === "1" ? it.skip : it;

async function makeUserWithSession() {
  const userId = crypto.randomUUID();
  await env.DB.prepare(
    "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
  )
    .bind(userId, `${userId}@example.com`, "pbkdf2$1$AA==$AA==")
    .run();
  const session = await createSession(env.DB, userId);
  return { userId, token: session.token };
}

describe("gha-bot tokens", () => {
  it("creates, lists, and revokes a token scoped to its owner", async () => {
    const { token } = await makeUserWithSession();
    const auth = {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    };

    const created = await ghaBot.request(
      "/tokens",
      { method: "POST", headers: auth, body: JSON.stringify({}) },
      env,
    );
    expect(created.status).toBe(200);
    const { secret } = await created.json<{ secret: string }>();
    expect(secret).toBeTruthy();

    const list = await ghaBot.request("/tokens", { headers: auth }, env);
    const tokens =
      await list.json<Array<{ id: string; revoked_at: string | null }>>();
    expect(tokens).toHaveLength(1);
    expect(tokens[0]!.revoked_at).toBeNull();

    const revoke = await ghaBot.request(
      `/tokens/${tokens[0]!.id}/revoke`,
      { method: "POST", headers: auth },
      env,
    );
    expect(revoke.status).toBe(200);

    // Erro/borda: revogar de novo não acha linha ainda-não-revogada.
    const revokeAgain = await ghaBot.request(
      `/tokens/${tokens[0]!.id}/revoke`,
      { method: "POST", headers: auth },
      env,
    );
    expect(revokeAgain.status).toBe(404);
  });

  it("rejects unauthenticated requests", async () => {
    const list = await ghaBot.request("/tokens", {}, env);
    expect(list.status).toBe(401);

    const create = await ghaBot.request(
      "/tokens",
      { method: "POST", body: "{}" },
      env,
    );
    expect(create.status).toBe(401);
  });

  it("scopes tokens per user — one user never sees another's tokens", async () => {
    const { token: tokenA } = await makeUserWithSession();
    const { token: tokenB } = await makeUserWithSession();

    await ghaBot.request(
      "/tokens",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${tokenA}`,
          "Content-Type": "application/json",
        },
        body: "{}",
      },
      env,
    );

    const listB = await ghaBot.request(
      "/tokens",
      { headers: { Authorization: `Bearer ${tokenB}` } },
      env,
    );
    expect(await listB.json()).toEqual([]);
  });
});

describe("gha-bot settings", () => {
  it("returns null before any config is saved, then round-trips a PUT", async () => {
    const { token } = await makeUserWithSession();
    const auth = {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    };

    const before = await ghaBot.request("/settings", { headers: auth }, env);
    expect(await before.json()).toBeNull();

    const put = await ghaBot.request(
      "/settings",
      {
        method: "PUT",
        headers: auth,
        body: JSON.stringify({
          provider: "anthropic",
          model: "claude-sonnet-5",
          provider_api_key: "gha-bot-anthropic-key-abc",
          review_style: "strict",
        }),
      },
      env,
    );
    expect(put.status).toBe(200);

    const after = await ghaBot.request("/settings", { headers: auth }, env);
    const settings = await after.json<{
      provider: string;
      model: string;
      review_style: string;
    }>();
    expect(settings.provider).toBe("anthropic");
    expect(settings.model).toBe("claude-sonnet-5");
    expect(settings.review_style).toBe("strict");
    // A chave/referência nunca volta na resposta do painel.
    expect(settings).not.toHaveProperty("provider_api_key");
  });

  it("rejects missing fields and an invalid review_style — erro/borda", async () => {
    const { token } = await makeUserWithSession();
    const auth = {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    };

    const missing = await ghaBot.request(
      "/settings",
      {
        method: "PUT",
        headers: auth,
        body: JSON.stringify({ provider: "anthropic" }),
      },
      env,
    );
    expect(missing.status).toBe(400);

    const badStyle = await ghaBot.request(
      "/settings",
      {
        method: "PUT",
        headers: auth,
        body: JSON.stringify({
          provider: "anthropic",
          model: "claude-sonnet-5",
          provider_api_key: "ref",
          review_style: "chaotic",
        }),
      },
      env,
    );
    expect(badStyle.status).toBe(400);
  });

  it("a second PUT overwrites the first, not a duplicate row", async () => {
    const { token, userId } = await makeUserWithSession();
    const auth = {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    };

    for (const model of ["claude-sonnet-5", "claude-opus-5"]) {
      await ghaBot.request(
        "/settings",
        {
          method: "PUT",
          headers: auth,
          body: JSON.stringify({
            provider: "anthropic",
            model,
            provider_api_key: "ref",
          }),
        },
        env,
      );
    }

    const { results } = await env.DB.prepare(
      "SELECT model FROM gha_bot_config WHERE user_id = ?",
    )
      .bind(userId)
      .all<{ model: string }>();
    expect(results).toHaveLength(1);
    expect(results[0]!.model).toBe("claude-opus-5");
  });
});

describe("gha-bot download (Action pública, sem auth — só o binário)", () => {
  it("devolve 404 quando nenhuma versão foi publicada ainda", async () => {
    const res = await ghaBot.request("/download/latest", {}, env);
    expect(res.status).toBe(404);
  });

  it("devolve a versão mais recente por comparação numérica, não lexicográfica", async () => {
    // "0.1.9" > "0.1.10" lexicograficamente, mas 0.1.10 é a mais recente —
    // se a rota comparasse como string, devolveria a errada.
    await env.R2.put("gha-bot/0.1.9/vectora-cli-linux-x64.tar.gz", "v9");
    await env.R2.put("gha-bot/0.1.10/vectora-cli-linux-x64.tar.gz", "v10");

    const res = await ghaBot.request("/download/latest", {}, env);
    expect(res.status).toBe(200);
    expect(res.headers.get("X-Vectora-Version")).toBe("0.1.10");
    expect(res.headers.get("Content-Type")).toBe("application/gzip");
    expect(await res.text()).toBe("v10");
  });
});

describe("gha-bot config (Action pública, autenticada por VECTORA_BOT_TOKEN)", () => {
  async function makeProUserWithBotToken() {
    const { userId, token: sessionToken } = await makeUserWithSession();
    await env.DB.prepare(
      "INSERT INTO subscriptions (id, user_id, tier, status) VALUES (?, ?, 'pro', 'active')",
    )
      .bind(crypto.randomUUID(), userId)
      .run();

    const created = await ghaBot.request(
      "/tokens",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${sessionToken}`,
          "Content-Type": "application/json",
        },
        body: "{}",
      },
      env,
    );
    const { secret: botToken } = await created.json<{ secret: string }>();
    return { userId, sessionToken, botToken };
  }

  it("devolve a chave real (decifrada) pra um usuário Pro configurado", async () => {
    const { sessionToken, botToken } = await makeProUserWithBotToken();

    await ghaBot.request(
      "/settings",
      {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${sessionToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          provider: "anthropic",
          model: "claude-sonnet-5",
          provider_api_key: "sk-ant-super-secreta-de-verdade",
          review_style: "lenient",
        }),
      },
      env,
    );

    const res = await ghaBot.request(
      "/config",
      { headers: { Authorization: `Bearer ${botToken}` } },
      env,
    );
    expect(res.status).toBe(200);
    const config = await res.json<{
      mode: string;
      provider: string;
      model: string;
      api_key: string;
      review_style: string;
    }>();
    expect(config).toEqual({
      mode: "hosted",
      provider: "anthropic",
      model: "claude-sonnet-5",
      api_key: "sk-ant-super-secreta-de-verdade",
      review_style: "lenient",
    });
  });

  it("rejeita token inválido/revogado — erro/borda", async () => {
    const unknown = await ghaBot.request(
      "/config",
      { headers: { Authorization: "Bearer token-que-nao-existe" } },
      env,
    );
    expect(unknown.status).toBe(401);

    const { sessionToken, botToken } = await makeProUserWithBotToken();
    const listed = await ghaBot.request(
      "/tokens",
      { headers: { Authorization: `Bearer ${sessionToken}` } },
      env,
    );
    const listedTokens = await listed.json<Array<{ id: string }>>();
    const id = listedTokens[0]!.id;
    await ghaBot.request(
      `/tokens/${id}/revoke`,
      { method: "POST", headers: { Authorization: `Bearer ${sessionToken}` } },
      env,
    );

    const revoked = await ghaBot.request(
      "/config",
      { headers: { Authorization: `Bearer ${botToken}` } },
      env,
    );
    expect(revoked.status).toBe(401);
  });

  it("rejeita usuário sem Pro, mesmo com token válido e config salva — erro/borda", async () => {
    const { userId, token: sessionToken } = await makeUserWithSession();
    const auth = {
      Authorization: `Bearer ${sessionToken}`,
      "Content-Type": "application/json",
    };
    await ghaBot.request(
      "/settings",
      {
        method: "PUT",
        headers: auth,
        body: JSON.stringify({
          provider: "anthropic",
          model: "claude-sonnet-5",
          provider_api_key: "sk-ant-x",
        }),
      },
      env,
    );
    const created = await ghaBot.request(
      "/tokens",
      { method: "POST", headers: auth, body: "{}" },
      env,
    );
    const { secret: botToken } = await created.json<{ secret: string }>();
    void userId;

    const res = await ghaBot.request(
      "/config",
      { headers: { Authorization: `Bearer ${botToken}` } },
      env,
    );
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: "pro_required" });
  });

  it("devolve not_configured pra usuário Pro sem settings salvas ainda — erro/borda", async () => {
    const { botToken } = await makeProUserWithBotToken();

    const res = await ghaBot.request(
      "/config",
      { headers: { Authorization: `Bearer ${botToken}` } },
      env,
    );
    expect(res.status).toBe(404);
  });
});

describe("gha-bot self-hosted (GET /config, POST/GET /review, POST /review/:id/result)", () => {
  async function makeProUserWithBotTokenAndSettings(selfHosted: boolean) {
    const { userId, token: sessionToken } = await makeUserWithSession();
    await env.DB.prepare(
      "INSERT INTO subscriptions (id, user_id, tier, status) VALUES (?, ?, 'pro', 'active')",
    )
      .bind(crypto.randomUUID(), userId)
      .run();

    const sessionAuth = {
      Authorization: `Bearer ${sessionToken}`,
      "Content-Type": "application/json",
    };
    const created = await ghaBot.request(
      "/tokens",
      { method: "POST", headers: sessionAuth, body: "{}" },
      env,
    );
    const { secret: botToken } = await created.json<{ secret: string }>();

    await ghaBot.request(
      "/settings",
      {
        method: "PUT",
        headers: sessionAuth,
        body: JSON.stringify({
          provider: "anthropic",
          model: "claude-sonnet-5",
          provider_api_key: "sk-ant-fallback",
          review_style: "balanced",
          self_hosted_enabled: selfHosted,
        }),
      },
      env,
    );

    return { userId, sessionToken, botToken };
  }

  async function registerGatewayToken(userId: string, gwToken: string) {
    await env.DB.prepare(
      "INSERT INTO tokens (id, user_id, token, token_hash) VALUES (?, ?, ?, ?)",
    )
      .bind(crypto.randomUUID(), userId, gwToken, `hash-${gwToken}`)
      .run();
  }

  // Além da linha em `tokens` (o que os testes acima já cobrem via
  // registerGatewayToken), abrir o WebSocket como dono da sessão agora
  // exige um connector_secret que bata com o secretHash guardado na DO
  // (ver achado de segurança do túnel — handleWebSocketUpgrade rejeita
  // sem isso). Fora do fluxo real de POST /register, simula o mesmo
  // passo interno (`/_set-secret`) que /register faz, devolvendo o
  // secret em texto puro pro teste usar no Authorization do handshake.
  const CONNECTOR_SECRET = "test-connector-secret-para-ws-gha-bot";

  async function registerGatewayTokenWithSecret(
    userId: string,
    gwToken: string,
  ): Promise<string> {
    await registerGatewayToken(userId, gwToken);
    const secretHash = await hashConnectorSecret(CONNECTOR_SECRET);
    const setSecretResp = await SELF.fetch(
      `https://${gwToken}.vectora.chat/_set-secret`,
      {
        method: "POST",
        headers: {
          "X-Vectora-Internal": `Bearer ${env.GATEWAY_INTERNAL_SECRET}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ secretHash }),
      },
    );
    expect(setSecretResp.ok).toBe(true);
    return CONNECTOR_SECRET;
  }

  it("self_hosted_enabled=false — /config sempre devolve mode hosted, nunca consulta o gateway", async () => {
    const { userId, botToken } =
      await makeProUserWithBotTokenAndSettings(false);
    await registerGatewayToken(userId, "tokenabc1");

    const res = await ghaBot.request(
      "/config",
      { headers: { Authorization: `Bearer ${botToken}` } },
      env,
    );
    const body = await res.json<{ mode: string }>();
    expect(body.mode).toBe("hosted");
  });

  it("erro de borda — self_hosted_enabled=true mas usuário nunca registrou o gateway (sem linha em tokens) — cai pra hosted", async () => {
    const { botToken } = await makeProUserWithBotTokenAndSettings(true);

    const res = await ghaBot.request(
      "/config",
      { headers: { Authorization: `Bearer ${botToken}` } },
      env,
    );
    const body = await res.json<{ mode: string }>();
    expect(body.mode).toBe("hosted");
  });

  itDO(
    "erro de borda — self_hosted_enabled=true e token registrado, mas instância offline (DO sem WS ativo) — cai pra hosted",
    async () => {
      const { userId, botToken } =
        await makeProUserWithBotTokenAndSettings(true);
      await registerGatewayToken(userId, "tokenoffline1");

      const res = await ghaBot.request(
        "/config",
        { headers: { Authorization: `Bearer ${botToken}` } },
        env,
      );
      const body = await res.json<{ mode: string }>();
      expect(body.mode).toBe("hosted");
    },
  );

  itDO(
    "self_hosted_enabled=true, token registrado, gateway conectado — /config devolve mode self-hosted com job_endpoint",
    async () => {
      const { userId, botToken } =
        await makeProUserWithBotTokenAndSettings(true);
      const gwToken = "tokenconnected1";
      const secret = await registerGatewayTokenWithSecret(userId, gwToken);

      const ws = await SELF.fetch(
        `https://gateway.vectora.chat/ws/${gwToken}`,
        {
          headers: {
            Upgrade: "websocket",
            Authorization: `Bearer ${secret}`,
          },
        },
      );
      expect(ws.status).toBe(101);
      expect(ws.webSocket).toBeTruthy();
      ws.webSocket!.accept();

      const res = await ghaBot.request(
        "/config",
        { headers: { Authorization: `Bearer ${botToken}` } },
        env,
      );
      const body = await res.json<{ mode: string; job_endpoint: string }>();
      expect(body.mode).toBe("self-hosted");
      expect(body.job_endpoint).toContain("/gha-bot/review");

      ws.webSocket!.close();
    },
  );

  itDO(
    "POST /review entrega o job pelo túnel (WS recebe review_job) e devolve 202 + job_id",
    async () => {
      const { userId, botToken } =
        await makeProUserWithBotTokenAndSettings(true);
      const gwToken = "tokenreview1";
      const secret = await registerGatewayTokenWithSecret(userId, gwToken);

      const ws = await SELF.fetch(
        `https://gateway.vectora.chat/ws/${gwToken}`,
        {
          headers: {
            Upgrade: "websocket",
            Authorization: `Bearer ${secret}`,
          },
        },
      );
      ws.webSocket!.accept();
      const received: MessageEvent[] = [];
      ws.webSocket!.addEventListener("message", (e) => received.push(e));

      const res = await ghaBot.request(
        "/review",
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${botToken}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            diff: "diff --git a/a.py b/a.py\n+print(1)",
            metadata: { pr_number: "42" },
          }),
        },
        env,
      );
      expect(res.status).toBe(202);
      const { job_id } = await res.json<{ job_id: string }>();
      expect(job_id).toBeTruthy();

      await vi.waitFor(() => expect(received).toHaveLength(1), {
        timeout: 2000,
      });
      const msg = JSON.parse(received[0]!.data as string) as {
        type: string;
        job_id: string;
        diff: string;
        metadata: Record<string, string>;
        callback_secret: string;
      };
      expect(msg.type).toBe("review_job");
      expect(msg.job_id).toBe(job_id);
      expect(msg.diff).toBe("diff --git a/a.py b/a.py\n+print(1)");
      expect(msg.metadata).toEqual({ pr_number: "42" });
      // Não distribuído em lugar nenhum além deste payload pelo túnel — ver
      // achado de segurança em POST /review/:id/result.
      expect(msg.callback_secret).toBeTruthy();

      const jobRow = await env.DB.prepare(
        "SELECT status, callback_secret_hash FROM gha_bot_review_jobs WHERE id = ?",
      )
        .bind(job_id)
        .first<{ status: string; callback_secret_hash: string }>();
      expect(jobRow?.status).toBe("pending");
      expect(jobRow?.callback_secret_hash).toBe(
        await sha256Hex(msg.callback_secret),
      );
      expect(jobRow?.callback_secret_hash).not.toContain(msg.callback_secret);

      ws.webSocket!.close();
    },
  );

  it("erro de borda — POST /review sem gateway registrado devolve 409, nenhum job criado", async () => {
    const { botToken } = await makeProUserWithBotTokenAndSettings(true);

    const res = await ghaBot.request(
      "/review",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${botToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ diff: "x" }),
      },
      env,
    );
    expect(res.status).toBe(409);
  });

  itDO(
    "erro de borda — POST /review com instância offline marca o job como failed e devolve 502",
    async () => {
      const { userId, botToken } =
        await makeProUserWithBotTokenAndSettings(true);
      await registerGatewayToken(userId, "tokenreviewoffline1");

      const res = await ghaBot.request(
        "/review",
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${botToken}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ diff: "x" }),
        },
        env,
      );
      expect(res.status).toBe(502);
      const { job_id } = await res.json<{ job_id: string }>();

      const jobRow = await env.DB.prepare(
        "SELECT status, error FROM gha_bot_review_jobs WHERE id = ?",
      )
        .bind(job_id)
        .first<{ status: string; error: string | null }>();
      expect(jobRow?.status).toBe("failed");
      expect(jobRow?.error).toBeTruthy();
    },
  );

  it("erro de borda — POST /review sem diff no corpo devolve 400", async () => {
    const { botToken } = await makeProUserWithBotTokenAndSettings(true);

    const res = await ghaBot.request(
      "/review",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${botToken}`,
          "Content-Type": "application/json",
        },
        body: "{}",
      },
      env,
    );
    expect(res.status).toBe(400);
  });

  it("GET /review/:id devolve o status do job — pending logo após criar", async () => {
    const { userId, botToken } = await makeProUserWithBotTokenAndSettings(true);
    await env.DB.prepare(
      "INSERT INTO gha_bot_review_jobs (id, user_id, callback_secret, callback_secret_hash, status) VALUES (?, ?, '', ?, 'pending')",
    )
      .bind("job-pending-1", userId, await sha256Hex("secret-pending-1"))
      .run();

    const res = await ghaBot.request(
      "/review/job-pending-1",
      { headers: { Authorization: `Bearer ${botToken}` } },
      env,
    );
    expect(res.status).toBe(200);
    const body = await res.json<{ status: string }>();
    expect(body.status).toBe("pending");
  });

  it("erro de borda — GET /review/:id de um job de outro usuário devolve 404 (isolamento)", async () => {
    const { userId: ownerId } = await makeProUserWithBotTokenAndSettings(true);
    const { botToken: strangerToken } =
      await makeProUserWithBotTokenAndSettings(true);
    await env.DB.prepare(
      "INSERT INTO gha_bot_review_jobs (id, user_id, callback_secret, callback_secret_hash, status) VALUES (?, ?, '', ?, 'pending')",
    )
      .bind("job-isolated-1", ownerId, await sha256Hex("secret-isolated-1"))
      .run();

    const res = await ghaBot.request(
      "/review/job-isolated-1",
      { headers: { Authorization: `Bearer ${strangerToken}` } },
      env,
    );
    expect(res.status).toBe(404);
  });

  it("POST /review/:id/result marca o job como done com o texto da revisão", async () => {
    const { userId, botToken } = await makeProUserWithBotTokenAndSettings(true);
    await env.DB.prepare(
      "INSERT INTO gha_bot_review_jobs (id, user_id, callback_secret, callback_secret_hash, status) VALUES (?, ?, '', ?, 'pending')",
    )
      .bind("job-result-1", userId, await sha256Hex("secret-result-1"))
      .run();

    const res = await ghaBot.request(
      "/review/job-result-1/result",
      {
        method: "POST",
        headers: {
          Authorization: "Bearer secret-result-1",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ review_text: "LGTM, só um nit na linha 3." }),
      },
      env,
    );
    expect(res.status).toBe(200);

    const poll = await ghaBot.request(
      "/review/job-result-1",
      { headers: { Authorization: `Bearer ${botToken}` } },
      env,
    );
    const body = await poll.json<{ status: string; review_text: string }>();
    expect(body.status).toBe("done");
    expect(body.review_text).toBe("LGTM, só um nit na linha 3.");
  });

  it("erro de borda — POST /review/:id/result com error marca o job como failed", async () => {
    const { userId, botToken } = await makeProUserWithBotTokenAndSettings(true);
    await env.DB.prepare(
      "INSERT INTO gha_bot_review_jobs (id, user_id, callback_secret, callback_secret_hash, status) VALUES (?, ?, '', ?, 'pending')",
    )
      .bind("job-result-err-1", userId, await sha256Hex("secret-result-err-1"))
      .run();

    const res = await ghaBot.request(
      "/review/job-result-err-1/result",
      {
        method: "POST",
        headers: {
          Authorization: "Bearer secret-result-err-1",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ error: "modelo indisponível" }),
      },
      env,
    );
    expect(res.status).toBe(200);

    const jobRow = await env.DB.prepare(
      "SELECT status, error FROM gha_bot_review_jobs WHERE id = ?",
    )
      .bind("job-result-err-1")
      .first<{ status: string; error: string }>();
    expect(jobRow?.status).toBe("failed");
    expect(jobRow?.error).toBe("modelo indisponível");
  });

  it("erro de borda — POST /review/:id/result não sobrescreve um job que já não está pending", async () => {
    const { userId, botToken } = await makeProUserWithBotTokenAndSettings(true);
    await env.DB.prepare(
      "INSERT INTO gha_bot_review_jobs (id, user_id, callback_secret, callback_secret_hash, status, review_text) VALUES (?, ?, '', ?, 'done', 'primeira resposta')",
    )
      .bind(
        "job-already-done-1",
        userId,
        await sha256Hex("secret-already-done-1"),
      )
      .run();

    const res = await ghaBot.request(
      "/review/job-already-done-1/result",
      {
        method: "POST",
        headers: {
          Authorization: "Bearer secret-already-done-1",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ review_text: "resposta duplicada" }),
      },
      env,
    );
    expect(res.status).toBe(404);

    const jobRow = await env.DB.prepare(
      "SELECT review_text FROM gha_bot_review_jobs WHERE id = ?",
    )
      .bind("job-already-done-1")
      .first<{ review_text: string }>();
    expect(jobRow?.review_text).toBe("primeira resposta");
  });

  it("rejeita /review e /review/:id sem VECTORA_BOT_TOKEN válido", async () => {
    const post = await ghaBot.request(
      "/review",
      { method: "POST", body: "{}" },
      env,
    );
    expect(post.status).toBe(401);

    const get = await ghaBot.request("/review/whatever", {}, env);
    expect(get.status).toBe(401);
  });

  it("erro de borda — POST /review/:id/result sem Authorization devolve 401 antes de tocar o banco", async () => {
    const result = await ghaBot.request(
      "/review/job-nao-existe/result",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ review_text: "x" }),
      },
      env,
    );
    expect(result.status).toBe(401);
  });

  it("erro de borda — POST /review/:id/result com callback_secret errado devolve 404, não sobrescreve o job", async () => {
    // Achado de segurança: job_id sozinho (visível em log de workflow) não
    // pode bastar pra escrever review_text arbitrário — precisa também
    // acertar o callback_secret gerado no INSERT.
    const { userId } = await makeProUserWithBotTokenAndSettings(true);
    await env.DB.prepare(
      "INSERT INTO gha_bot_review_jobs (id, user_id, callback_secret, callback_secret_hash, status) VALUES (?, ?, '', ?, 'pending')",
    )
      .bind("job-secret-errado-1", userId, await sha256Hex("secret-certo"))
      .run();

    const res = await ghaBot.request(
      "/review/job-secret-errado-1/result",
      {
        method: "POST",
        headers: {
          Authorization: "Bearer secret-errado",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ review_text: "review forjado" }),
      },
      env,
    );
    expect(res.status).toBe(404);

    const jobRow = await env.DB.prepare(
      "SELECT status, review_text FROM gha_bot_review_jobs WHERE id = ?",
    )
      .bind("job-secret-errado-1")
      .first<{ status: string; review_text: string | null }>();
    expect(jobRow?.status).toBe("pending");
    expect(jobRow?.review_text).toBeNull();
  });

  it("erro de borda — POST /review/:id/result de um job inexistente com qualquer secret devolve 404", async () => {
    const result = await ghaBot.request(
      "/review/job-nao-existe/result",
      {
        method: "POST",
        headers: {
          Authorization: "Bearer qualquer-coisa",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ review_text: "x" }),
      },
      env,
    );
    expect(result.status).toBe(404);
  });
});

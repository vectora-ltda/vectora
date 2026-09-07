/**
 * oauth/ — porta company/src/server/fns/oauth.ts (authorizeDevice). O gateway
 * (ex-relay) roda no mesmo Worker (ver src/gateway/), mas continua exposto só
 * via HTTP em gateway.vectora.chat — chamamos por fetch normal, não por
 * acesso direto ao módulo, pra não acoplar o dispatch por hostname a uma
 * chamada interna.
 */
import { Hono } from "hono";
import type { Env } from "../gateway/types";
import { requireUserId } from "../auth/routes";

const OAUTH_TTL_SECONDS = 300;
const OAUTH_STATE_PATTERN = /^[A-Za-z0-9_-]{32,128}$/;
const OAUTH_TRANSACTION_COOKIE = "vectora_oauth_transaction";

type Provider = "github" | "gitlab" | "google";
type OAuthConfig = {
  clientId: string;
  clientSecret: string;
  authorizeUrl: string;
  tokenUrl: string;
  scopes: string;
  callbackPath: string;
};

function configFor(provider: string, env: Env): OAuthConfig | null {
  if (
    provider === "github" &&
    env.GITHUB_OAUTH_CLIENT_ID &&
    env.GITHUB_OAUTH_CLIENT_SECRET
  ) {
    return {
      clientId: env.GITHUB_OAUTH_CLIENT_ID,
      clientSecret: env.GITHUB_OAUTH_CLIENT_SECRET,
      authorizeUrl: "https://github.com/login/oauth/authorize",
      tokenUrl: "https://github.com/login/oauth/access_token",
      scopes: "repo,user:email,read:org",
      callbackPath: "/oauth/integrations/github/callback",
    };
  }
  if (
    provider === "gitlab" &&
    env.GITLAB_OAUTH_CLIENT_ID &&
    env.GITLAB_OAUTH_CLIENT_SECRET
  ) {
    const base = env.GITLAB_BASE_URL ?? "https://gitlab.com";
    return {
      clientId: env.GITLAB_OAUTH_CLIENT_ID,
      clientSecret: env.GITLAB_OAUTH_CLIENT_SECRET,
      authorizeUrl: `${base}/oauth/authorize`,
      tokenUrl: `${base}/oauth/token`,
      scopes: "api read_repository write_repository read_user",
      callbackPath: "/oauth/integrations/gitlab/callback",
    };
  }
  if (
    provider === "google" &&
    env.GOOGLE_OAUTH_CLIENT_ID &&
    env.GOOGLE_OAUTH_CLIENT_SECRET
  ) {
    return {
      clientId: env.GOOGLE_OAUTH_CLIENT_ID,
      clientSecret: env.GOOGLE_OAUTH_CLIENT_SECRET,
      authorizeUrl: "https://accounts.google.com/o/oauth2/v2/auth",
      tokenUrl: "https://oauth2.googleapis.com/token",
      scopes:
        "openid email profile https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/gmail.readonly",
      callbackPath: "/oauth/integrations/google/callback",
    };
  }
  return null;
}

function stateKey(kind: "pending" | "result", state: string): string {
  return `oauth:integration:${kind}:${state}`;
}

async function storeOAuthResult(
  env: Env,
  state: string,
  value: string,
): Promise<void> {
  const id = env.OAUTH_RESULT.idFromName(state);
  await env.OAUTH_RESULT.get(id).fetch(
    `https://oauth-result/?key=${encodeURIComponent(stateKey("result", state))}`,
    {
      method: "POST",
      body: JSON.stringify({ value, expirationTtl: OAUTH_TTL_SECONDS }),
    },
  );
}

async function storeOAuthState(
  env: Env,
  state: string,
  value: string,
): Promise<void> {
  const id = env.OAUTH_RESULT.idFromName(state);
  await env.OAUTH_RESULT.get(id).fetch(
    `https://oauth-result/?key=${encodeURIComponent(stateKey("pending", state))}`,
    {
      method: "POST",
      body: JSON.stringify({ value, expirationTtl: OAUTH_TTL_SECONDS }),
    },
  );
}

async function deleteOAuthState(env: Env, state: string): Promise<void> {
  const id = env.OAUTH_RESULT.idFromName(state);
  await env.OAUTH_RESULT.get(id).fetch(
    `https://oauth-result/?key=${encodeURIComponent(stateKey("pending", state))}`,
    { method: "DELETE" },
  );
}

async function finishOAuthWithError(
  env: Env,
  state: string,
  provider: string,
  returnTo: string,
  error: string,
): Promise<Response> {
  await storeOAuthResult(env, state, JSON.stringify({ provider, error }));
  await deleteOAuthState(env, state);
  return redirectWithTransactionCookie(returnTo, state);
}

function allowedReturnTo(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      (url.hostname === "vectora.chat" ||
        url.hostname.endsWith(".vectora.chat"))
    );
  } catch {
    return false;
  }
}

function withState(returnTo: string, state: string): string {
  const url = new URL(returnTo);
  url.searchParams.set("state", state);
  return url.toString();
}

function redirectWithTransactionCookie(
  returnTo: string,
  state: string,
): Response {
  return new Response(null, {
    status: 302,
    headers: {
      Location: withState(returnTo, state),
      "Set-Cookie": `${OAUTH_TRANSACTION_COOKIE}=${encodeURIComponent(state)}; Max-Age=${OAUTH_TTL_SECONDS}; Path=/; Domain=.vectora.chat; HttpOnly; Secure; SameSite=Lax`,
    },
  });
}

export const oauth = new Hono<{ Bindings: Env }>();

oauth.get("/integrations/providers", (c) => {
  const providers = (["github", "gitlab", "google"] as const).filter(
    (provider) => configFor(provider, c.env) !== null,
  );
  return c.json({ providers });
});

oauth.get("/integrations/:provider/start", async (c) => {
  const provider = c.req.param("provider");
  const state = c.req.query("state") ?? "";
  const returnTo = c.req.query("return_to") ?? "";
  const config = configFor(provider, c.env);
  if (!config) return c.json({ error: "provider_not_configured" }, 503);
  if (!OAUTH_STATE_PATTERN.test(state))
    return c.json({ error: "invalid_state" }, 400);
  if (!allowedReturnTo(returnTo))
    return c.json({ error: "invalid_return_to" }, 400);

  await storeOAuthState(c.env, state, JSON.stringify({ provider, returnTo }));
  const callback = new URL(
    config.callbackPath,
    c.env.OAUTH_PUBLIC_URL ?? c.env.APP_URL,
  ).toString();
  const authorize = new URL(config.authorizeUrl);
  authorize.searchParams.set("client_id", config.clientId);
  authorize.searchParams.set("redirect_uri", callback);
  authorize.searchParams.set("response_type", "code");
  authorize.searchParams.set("scope", config.scopes);
  authorize.searchParams.set("state", state);
  if (provider === "google")
    authorize.searchParams.set("access_type", "offline");
  return c.redirect(authorize.toString());
});

oauth.get("/integrations/:provider/callback", async (c) => {
  const provider = c.req.param("provider");
  const state = c.req.query("state") ?? "";
  const code = c.req.query("code") ?? "";
  const config = configFor(provider, c.env);
  const pending = state
    ? await c.env.OAUTH_RESULT.get(c.env.OAUTH_RESULT.idFromName(state))
        .fetch(
          `https://oauth-result/?key=${encodeURIComponent(stateKey("pending", state))}&provider=${encodeURIComponent(provider)}&action=claim`,
          { method: "POST", body: "{}" },
        )
        .then(async (response) =>
          response.status === 202
            ? null
            : ((await response.json()) as {
                provider: string;
                returnTo: string;
              }),
        )
    : null;
  if (!config || !pending || pending.provider !== provider) {
    return c.text("OAuth state expired or invalid", 400);
  }
  if (!code) {
    const reason = c.req.query("error") ?? "authorization_denied";
    return finishOAuthWithError(
      c.env,
      state,
      provider,
      pending.returnTo,
      reason,
    );
  }

  const body = new URLSearchParams({
    client_id: config.clientId,
    client_secret: config.clientSecret,
    code,
    redirect_uri: new URL(
      config.callbackPath,
      c.env.OAUTH_PUBLIC_URL ?? c.env.APP_URL,
    ).toString(),
    grant_type: "authorization_code",
  });
  let response: Response;
  let payload: Record<string, unknown>;
  try {
    response = await fetch(config.tokenUrl, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body,
    });
    if (!response.ok) {
      console.error("oauth_exchange_failed", {
        provider,
        status: response.status,
      });
      return finishOAuthWithError(
        c.env,
        state,
        provider,
        pending.returnTo,
        "token_missing",
      );
    }
    payload = (await response.json()) as Record<string, unknown>;
  } catch (error) {
    console.error("oauth_exchange_transport_failed", {
      provider,
      error: error instanceof Error ? error.message : String(error),
    });
    return finishOAuthWithError(
      c.env,
      state,
      provider,
      pending.returnTo,
      "token_exchange_failed",
    );
  }
  const accessToken =
    typeof payload.access_token === "string"
      ? payload.access_token
      : typeof payload.authed_user === "object" && payload.authed_user !== null
        ? ((payload.authed_user as { access_token?: string }).access_token ??
          "")
        : "";
  if (!accessToken) {
    console.error("oauth_exchange_missing_token", { provider });
    return finishOAuthWithError(
      c.env,
      state,
      provider,
      pending.returnTo,
      "token_missing",
    );
  }
  await storeOAuthResult(
    c.env,
    state,
    JSON.stringify({
      provider,
      accessToken,
      refreshToken: payload.refresh_token ?? null,
    }),
  );
  await deleteOAuthState(c.env, state);
  return redirectWithTransactionCookie(pending.returnTo, state);
});

oauth.get("/integrations/:provider/result/:state", async (c) => {
  if (
    c.req.header("Authorization") !== `Bearer ${c.env.VECTORA_OAUTH_SECRET}`
  ) {
    return c.json({ error: "unauthorized" }, 401);
  }
  const provider = c.req.param("provider");
  const state = c.req.param("state");
  const id = c.env.OAUTH_RESULT.idFromName(state);
  const response = await c.env.OAUTH_RESULT.get(id).fetch(
    `https://oauth-result/?key=${encodeURIComponent(stateKey("result", state))}&provider=${encodeURIComponent(provider)}`,
  );
  if (response.status === 202) return c.body(null, 202);
  if (!response.ok) return c.json({ error: "oauth_result_error" }, 502);
  const result = (await response.json()) as { provider?: string };
  if (result.provider !== provider)
    return c.json({ error: "provider_mismatch" }, 400);
  return c.json(result);
});

oauth.post("/device", async (c) => {
  const userId = await requireUserId(c);
  if (!userId) return c.json({ error: "unauthorized" }, 401);

  const body = await c.req.json<{ state?: string }>();
  if (!body.state) return c.json({ error: "state_required" }, 400);

  const row = await c.env.DB.prepare(
    "SELECT token FROM tokens WHERE user_id = ?",
  )
    .bind(userId)
    .first<{ token: string | null }>();
  if (!row?.token) return c.json({ error: "no_token" }, 409);

  const resp = await fetch(`${c.env.GATEWAY_URL}/oauth/token`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${c.env.VECTORA_OAUTH_SECRET}`,
    },
    body: JSON.stringify({ state: body.state, token: row.token }),
  });
  if (!resp.ok) return c.json({ error: "gateway_error" }, 502);

  return c.json({ ok: true });
});

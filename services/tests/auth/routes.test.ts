import { env } from "cloudflare:test";
import { describe, expect, it, vi, afterEach } from "vitest";
import { auth, requireUserId } from "../../src/auth/routes";
import { createSession, sha256Hex } from "../../src/auth/session";

afterEach(() => {
  vi.unstubAllGlobals();
});

function signupBody(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    name: "Ada Lovelace",
    email: `ada-${crypto.randomUUID()}@example.com`,
    password: "correct horse battery staple",
    country: "INTL",
    turnstileToken: "test-token",
    ...overrides,
  };
}

async function post(
  path: string,
  body: unknown,
  headers: Record<string, string> = {},
) {
  return auth.request(
    path,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers },
      body: JSON.stringify(body),
    },
    env,
  );
}

describe("POST /signup", () => {
  it("creates the user + free subscription + recoverable token, and rejects a duplicate email", async () => {
    const body = signupBody();
    const res = await post("/signup", body);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      needsConfirmation: true,
      email: body.email,
    });

    const user = await env.DB.prepare(
      "SELECT id, email_verified FROM users WHERE email = ?",
    )
      .bind(body.email)
      .first<{ id: string; email_verified: number }>();
    expect(user?.email_verified).toBe(0);

    const sub = await env.DB.prepare(
      "SELECT tier, status FROM subscriptions WHERE user_id = ?",
    )
      .bind(user!.id)
      .first<{ tier: string; status: string }>();
    expect(sub).toEqual({ tier: "free", status: "active" });

    const token = await env.DB.prepare(
      "SELECT token FROM tokens WHERE user_id = ?",
    )
      .bind(user!.id)
      .first<{ token: string | null }>();
    expect(token?.token).not.toBeNull();

    const dup = await post("/signup", signupBody({ email: body.email }));
    expect(dup.status).toBe(409);
    expect(await dup.json()).toEqual({ error: "email_taken" });
  });

  it("rejects a missing/short name, invalid email, short password, and missing turnstile token in the same request shape", async () => {
    expect((await post("/signup", signupBody({ name: "A" }))).status).toBe(400);
    expect(
      (await post("/signup", signupBody({ email: "not-an-email" }))).status,
    ).toBe(400);
    expect(
      (await post("/signup", signupBody({ password: "short" }))).status,
    ).toBe(400);
    const res = await post(
      "/signup",
      signupBody({ turnstileToken: undefined }),
    );
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "turnstile_required" });
  });
});

describe("POST /signup — pending gift", () => {
  it("claims a pending gift for the email, granting Pro with the gift's duration", async () => {
    const adminId = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO users (id, email, password_hash, role) VALUES (?, ?, ?, 'admin')",
    )
      .bind(adminId, `${adminId}@example.com`, "pbkdf2$1$AA==$AA==")
      .run();
    const body = signupBody();
    const giftId = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO gifts (id, email, granted_by, duration_months) VALUES (?, ?, ?, 6)",
    )
      .bind(giftId, body.email, adminId)
      .run();

    const res = await post("/signup", body);
    expect(res.status).toBe(200);

    const user = await env.DB.prepare("SELECT id FROM users WHERE email = ?")
      .bind(body.email)
      .first<{ id: string }>();
    const sub = await env.DB.prepare(
      "SELECT tier, status, provider, current_period_end FROM subscriptions WHERE user_id = ?",
    )
      .bind(user!.id)
      .first<{
        tier: string;
        status: string;
        provider: string;
        current_period_end: string;
      }>();
    expect(sub?.tier).toBe("pro");
    expect(sub?.provider).toBe("gift");
    expect(sub?.current_period_end).not.toBeNull();

    const gift = await env.DB.prepare(
      "SELECT status, claimed_user_id FROM gifts WHERE id = ?",
    )
      .bind(giftId)
      .first<{ status: string; claimed_user_id: string }>();
    expect(gift).toEqual({ status: "claimed", claimed_user_id: user!.id });
  });

  it("leaves a fresh signup with no gift on the default free tier", async () => {
    const body = signupBody();
    await post("/signup", body);
    const user = await env.DB.prepare("SELECT id FROM users WHERE email = ?")
      .bind(body.email)
      .first<{ id: string }>();
    const sub = await env.DB.prepare(
      "SELECT tier FROM subscriptions WHERE user_id = ?",
    )
      .bind(user!.id)
      .first<{ tier: string }>();
    expect(sub?.tier).toBe("free");
  });
});

describe("POST /signup — country BR", () => {
  it("creates a BR user with a BRL subscription", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({}))),
    );
    const body = signupBody({ country: "BR" });
    const res = await post("/signup", body);
    expect(res.status).toBe(200);

    const user = await env.DB.prepare(
      "SELECT id, country FROM users WHERE email = ?",
    )
      .bind(body.email)
      .first<{ id: string; country: string }>();
    expect(user?.country).toBe("BR");

    const sub = await env.DB.prepare(
      "SELECT currency FROM subscriptions WHERE user_id = ?",
    )
      .bind(user!.id)
      .first<{ currency: string }>();
    expect(sub?.currency).toBe("BRL");
  });
});

describe("POST /verify", () => {
  async function createUnverifiedUserWithToken() {
    const userId = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
    )
      .bind(userId, `${userId}@example.com`, "pbkdf2$1$AA==$AA==")
      .run();
    const rawToken = crypto.randomUUID();
    const tokenHash = await sha256Hex(rawToken);
    await env.DB.prepare(
      "INSERT INTO email_verifications (id, user_id, token_hash, purpose, expires_at) VALUES (?, ?, ?, 'verify_email', ?)",
    )
      .bind(
        crypto.randomUUID(),
        userId,
        tokenHash,
        new Date(Date.now() + 60_000).toISOString(),
      )
      .run();
    return { userId, rawToken };
  }

  it("marks the user verified, opens a session, and rejects reuse of the same token", async () => {
    const { userId, rawToken } = await createUnverifiedUserWithToken();

    const res = await post("/verify", { token: rawToken });
    expect(res.status).toBe(200);
    const json = await res.json<{ session_token: string }>();
    expect(json.session_token).toBeTruthy();

    const user = await env.DB.prepare(
      "SELECT email_verified FROM users WHERE id = ?",
    )
      .bind(userId)
      .first<{ email_verified: number }>();
    expect(user?.email_verified).toBe(1);

    const reuse = await post("/verify", { token: rawToken });
    expect(reuse.status).toBe(410);
    expect(await reuse.json()).toEqual({ error: "token_already_used" });
  });

  it("rejects an unknown token and an expired token", async () => {
    expect((await post("/verify", { token: "never-issued" })).status).toBe(404);

    const { userId } = await createUnverifiedUserWithToken();
    const expiredToken = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO email_verifications (id, user_id, token_hash, purpose, expires_at) VALUES (?, ?, ?, 'verify_email', ?)",
    )
      .bind(
        crypto.randomUUID(),
        userId,
        await sha256Hex(expiredToken),
        new Date(Date.now() - 1000).toISOString(),
      )
      .run();
    const res = await post("/verify", { token: expiredToken });
    expect(res.status).toBe(410);
    expect(await res.json()).toEqual({ error: "token_expired" });
  });
});

describe("POST /login and GET /me", () => {
  it("logs in a verified user and resolves /me with the session token, rejecting bad credentials", async () => {
    const body = signupBody();
    await post("/signup", body);
    const userRow = await env.DB.prepare("SELECT id FROM users WHERE email = ?")
      .bind(body.email)
      .first<{ id: string }>();
    await env.DB.prepare("UPDATE users SET email_verified = 1 WHERE id = ?")
      .bind(userRow!.id)
      .run();

    const wrongPassword = await post("/login", {
      email: body.email,
      password: "wrong",
    });
    expect(wrongPassword.status).toBe(401);

    const login = await post("/login", {
      email: body.email,
      password: body.password,
    });
    expect(login.status).toBe(200);
    const { session_token: sessionToken } = await login.json<{
      session_token: string;
    }>();

    const me = await auth.request(
      "/me",
      { headers: { Authorization: `Bearer ${sessionToken}` } },
      env,
    );
    expect(me.status).toBe(200);
    const meJson = await me.json<{ email: string; role: string }>();
    expect(meJson.email).toBe(body.email);
    expect(meJson.role).toBe("user");

    const unauthenticated = await auth.request("/me", {}, env);
    expect(unauthenticated.status).toBe(401);
  });

  it("rejects login for an unverified user", async () => {
    const body = signupBody();
    await post("/signup", body);
    const res = await post("/login", {
      email: body.email,
      password: body.password,
    });
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: "email_not_verified" });
  });

  it("rejects a login request missing email or password", async () => {
    const res = await post("/login", { email: "a@b.com" });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({
      error: "email_and_password_required",
    });
  });
});

describe("POST /login rate limiting", () => {
  it("blocks with 429 after exceeding the per-IP limit — brute force sem defesa antes desse fix", async () => {
    const ip = "203.0.113.12";
    const attempt = () =>
      post(
        "/login",
        { email: "nobody@example.com", password: "wrong" },
        { "cf-connecting-ip": ip },
      );

    // Limite configurado é 10/60s — 11 tentativas do mesmo IP garantem
    // estourar. Todas antes da 11ª devem ser 401 (credencial inválida,
    // não bloqueada); a partir dela, 429.
    const results: number[] = [];
    for (let i = 0; i < 11; i++) {
      results.push((await attempt()).status);
    }
    expect(results.filter((s) => s === 401).length).toBeGreaterThan(0);
    expect(results.filter((s) => s === 429).length).toBeGreaterThan(0);

    const limited = await attempt();
    expect(limited.status).toBe(429);
    expect(await limited.json()).toEqual({ error: "rate_limited" });
  });
});

describe("POST /logout", () => {
  it("revokes the session so it stops resolving, and is a no-op without a token", async () => {
    const body = signupBody();
    await post("/signup", body);
    const userRow = await env.DB.prepare("SELECT id FROM users WHERE email = ?")
      .bind(body.email)
      .first<{ id: string }>();
    await env.DB.prepare("UPDATE users SET email_verified = 1 WHERE id = ?")
      .bind(userRow!.id)
      .run();
    const login = await post("/login", {
      email: body.email,
      password: body.password,
    });
    const { session_token: sessionToken } = await login.json<{
      session_token: string;
    }>();

    const noToken = await auth.request("/logout", { method: "POST" }, env);
    expect(noToken.status).toBe(200);

    const logout = await auth.request(
      "/logout",
      { method: "POST", headers: { Authorization: `Bearer ${sessionToken}` } },
      env,
    );
    expect(logout.status).toBe(200);

    const me = await auth.request(
      "/me",
      { headers: { Authorization: `Bearer ${sessionToken}` } },
      env,
    );
    expect(me.status).toBe(401);
  });
});

describe("POST /magic-link", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("enqueues an email and never reveals whether the address exists", async () => {
    const body = signupBody();
    await post("/signup", body);

    const sendSpy = vi.spyOn(env.EMAIL_QUEUE, "send");

    const known = await post("/magic-link", { email: body.email });
    expect(known.status).toBe(200);
    expect(sendSpy).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ to: body.email }),
    );

    sendSpy.mockClear();
    const unknown = await post("/magic-link", { email: "nobody@example.com" });
    expect(unknown.status).toBe(200);
    expect(sendSpy).not.toHaveBeenCalled();
  });

  it("requires an email", async () => {
    const res = await post("/magic-link", {});
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "email_required" });
  });
});

describe("requireUserId", () => {
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

  it("authenticates via Bearer, or returns null without one", async () => {
    const { userId, token } = await makeUserWithSession();

    const viaBearer = await requireUserId({
      req: {
        raw: new Request("https://x.test", {
          headers: { Authorization: `Bearer ${token}` },
        }),
      },
      env,
    });
    expect(viaBearer).toBe(userId);

    const neither = await requireUserId({
      req: { raw: new Request("https://x.test") },
      env,
    });
    expect(neither).toBeNull();
  });
});

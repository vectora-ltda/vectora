import { env } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import { admin } from "../../src/admin/routes";
import { createSession } from "../../src/auth/session";

async function createUser(
  role: "user" | "admin" = "user",
  overrides: Partial<{ email: string; full_name: string }> = {},
) {
  const userId = crypto.randomUUID();
  await env.DB.prepare(
    "INSERT INTO users (id, email, password_hash, full_name, role) VALUES (?, ?, ?, ?, ?)",
  )
    .bind(
      userId,
      overrides.email ?? `${userId}@example.com`,
      "pbkdf2$1$AA==$AA==",
      overrides.full_name ?? "Test User",
      role,
    )
    .run();
  await env.DB.prepare(
    "INSERT INTO subscriptions (id, user_id, tier, status) VALUES (?, ?, 'free', 'active')",
  )
    .bind(crypto.randomUUID(), userId)
    .run();
  const session = await createSession(env.DB, userId);
  return { userId, token: session.token };
}

function authed(token: string, init: RequestInit = {}) {
  return {
    ...init,
    headers: { ...init.headers, Authorization: `Bearer ${token}` },
  };
}

describe("admin routes — access control", () => {
  it("rejects a non-admin (403) and an unauthenticated caller (403)", async () => {
    const { token } = await createUser("user");
    expect((await admin.request("/users", authed(token), env)).status).toBe(
      403,
    );
    expect((await admin.request("/users", {}, env)).status).toBe(403);
  });
});

describe("GET /admin/users", () => {
  it("lists users with their subscription, admin only", async () => {
    const { token: adminToken } = await createUser("admin");
    const { userId } = await createUser("user", {
      email: "listed@example.com",
    });

    const res = await admin.request("/users", authed(adminToken), env);
    expect(res.status).toBe(200);
    const body = await res.json<{
      users: Array<{ id: string; email: string }>;
    }>();
    expect(body.users.some((u) => u.id === userId)).toBe(true);
  });
});

describe("POST /admin/coupons", () => {
  it("creates a discount coupon, rejects a duplicate code, and validates required fields", async () => {
    const { token } = await createUser("admin");

    const res = await admin.request(
      "/coupons",
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code: "newcode",
          kind: "discount",
          grant_plan_id: "3m",
          charge_plan_id: "1m",
        }),
      }),
      env,
    );
    expect(res.status).toBe(200);
    expect((await res.json<{ code: string }>()).code).toBe("NEWCODE");

    const dup = await admin.request(
      "/coupons",
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code: "newcode",
          kind: "discount",
          grant_plan_id: "3m",
          charge_plan_id: "1m",
        }),
      }),
      env,
    );
    expect(dup.status).toBe(409);

    const missingPlans = await admin.request(
      "/coupons",
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: "other", kind: "discount" }),
      }),
      env,
    );
    expect(missingPlans.status).toBe(400);

    const invalidKind = await admin.request(
      "/coupons",
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: "other2", kind: "bogus" }),
      }),
      env,
    );
    expect(invalidKind.status).toBe(400);
  });

  it("creates a free_lifetime coupon without plan fields", async () => {
    const { token } = await createUser("admin");
    const res = await admin.request(
      "/coupons",
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: "SECRETONE", kind: "free_lifetime" }),
      }),
      env,
    );
    expect(res.status).toBe(200);
  });
});

describe("GET /admin/coupons and POST /:id/deactivate", () => {
  it("lists coupons and deactivates one by id, 404 for unknown id", async () => {
    const { token } = await createUser("admin");
    await admin.request(
      "/coupons",
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code: "TODEACTIVATE",
          kind: "discount",
          grant_plan_id: "3m",
          charge_plan_id: "1m",
        }),
      }),
      env,
    );

    const list = await admin.request("/coupons", authed(token), env);
    expect(list.status).toBe(200);
    const { coupons } = await list.json<{
      coupons: Array<{ id: string; code: string; active: number }>;
    }>();
    const created = coupons.find((c) => c.code === "TODEACTIVATE")!;
    expect(created.active).toBe(1);

    const deactivate = await admin.request(
      `/coupons/${created.id}/deactivate`,
      authed(token, { method: "POST" }),
      env,
    );
    expect(deactivate.status).toBe(200);

    const notFound = await admin.request(
      "/coupons/does-not-exist/deactivate",
      authed(token, { method: "POST" }),
      env,
    );
    expect(notFound.status).toBe(404);
  });
});

describe("POST /admin/gifts", () => {
  it("rejects an invalid email", async () => {
    const { token } = await createUser("admin");
    const res = await admin.request(
      "/gifts",
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: "not-an-email" }),
      }),
      env,
    );
    expect(res.status).toBe(400);
  });

  it("for an email without an account yet: records a pending gift and enqueues the email", async () => {
    const { token } = await createUser("admin", { full_name: "Bruno" });
    // EMAIL_QUEUE é o binding real do Miniflare — mockImplementation evita
    // que .send() dispare o consumer da fila (chamada real ao Resend).
    const sendSpy = vi
      .spyOn(env.EMAIL_QUEUE, "send")
      .mockImplementation(async () => undefined as never);

    const res = await admin.request(
      "/gifts",
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: "future-user@example.com" }),
      }),
      env,
    );
    expect(res.status).toBe(200);
    expect(await res.json<{ claimed: boolean }>()).toMatchObject({
      claimed: false,
    });
    expect(sendSpy).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ to: "future-user@example.com" }),
    );

    const gift = await env.DB.prepare(
      "SELECT status, duration_months FROM gifts WHERE email = ?",
    )
      .bind("future-user@example.com")
      .first<{ status: string; duration_months: number | null }>();
    expect(gift).toEqual({ status: "pending", duration_months: null });
    sendSpy.mockRestore();
  });

  it("for an existing account: grants Pro immediately with the given duration and marks the gift claimed", async () => {
    const { token } = await createUser("admin");
    const { userId } = await createUser("user", {
      email: "already-has-account@example.com",
    });

    const res = await admin.request(
      "/gifts",
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: "already-has-account@example.com",
          duration_months: 6,
        }),
      }),
      env,
    );
    expect(res.status).toBe(200);
    expect(await res.json<{ claimed: boolean }>()).toMatchObject({
      claimed: true,
    });

    const sub = await env.DB.prepare(
      "SELECT tier, status, provider, current_period_end FROM subscriptions WHERE user_id = ?",
    )
      .bind(userId)
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
      "SELECT status, claimed_user_id FROM gifts WHERE email = ?",
    )
      .bind("already-has-account@example.com")
      .first<{ status: string; claimed_user_id: string }>();
    expect(gift).toEqual({ status: "claimed", claimed_user_id: userId });
  });

  it("lists gifts already given", async () => {
    const { token } = await createUser("admin");
    await admin.request(
      "/gifts",
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: "listed-gift@example.com" }),
      }),
      env,
    );

    const res = await admin.request("/gifts", authed(token), env);
    expect(res.status).toBe(200);
    const { gifts } = await res.json<{ gifts: Array<{ email: string }> }>();
    expect(gifts.some((g) => g.email === "listed-gift@example.com")).toBe(true);
  });
});

async function createIssue(
  overrides: Partial<{ title: string; email: string | null }> = {},
) {
  const id = crypto.randomUUID();
  await env.DB.prepare(
    "INSERT INTO issues (id, title, category, description, email) VALUES (?, ?, 'bug', 'Descrição', ?)",
  )
    .bind(id, overrides.title ?? "Issue de admin", overrides.email ?? null)
    .run();
  return id;
}

describe("POST /admin/issues/:id/approve e /sync", () => {
  it("exige administrador nos dois endpoints", async () => {
    const { token } = await createUser("user");
    const approveResponse = await admin.request(
      `/issues/${crypto.randomUUID()}/approve`,
      authed(token, { method: "POST" }),
      env,
    );
    expect(approveResponse.status).toBe(403);
    expect(
      (
        await admin.request(
          `/issues/${crypto.randomUUID()}/sync`,
          authed(token, { method: "POST" }),
          env,
        )
      ).status,
    ).toBe(403);
  });

  it("retorna 404 para uma issue inexistente", async () => {
    const { token } = await createUser("admin");
    expect(
      (
        await admin.request(
          `/issues/${crypto.randomUUID()}/approve`,
          authed(token, { method: "POST" }),
          env,
        )
      ).status,
    ).toBe(404);
    expect(
      (
        await admin.request(
          `/issues/${crypto.randomUUID()}/sync`,
          authed(token, { method: "POST" }),
          env,
        )
      ).status,
    ).toBe(404);
  });
});

describe("GET /admin/issues e GET /admin/issues/:id", () => {
  it("lista/mostra o email do reporter (nunca exposto na rota pública) e rejeita não-admin", async () => {
    const { token } = await createUser("admin");
    const id = await createIssue({ email: "reporter@example.com" });

    const list = await admin.request("/issues", authed(token), env);
    expect(list.status).toBe(200);
    const { issues } = await list.json<{
      issues: Array<{ id: string; email: string | null }>;
    }>();
    expect(issues.find((i) => i.id === id)?.email).toBe("reporter@example.com");

    const detail = await admin.request(`/issues/${id}`, authed(token), env);
    expect(detail.status).toBe(200);
    expect((await detail.json<{ email: string | null }>()).email).toBe(
      "reporter@example.com",
    );

    const { token: userToken } = await createUser("user");
    expect(
      (await admin.request("/issues", authed(userToken), env)).status,
    ).toBe(403);
  });

  it("GET /admin/issues/:id com id inexistente → 404", async () => {
    const { token } = await createUser("admin");
    const res = await admin.request(
      `/issues/${crypto.randomUUID()}`,
      authed(token),
      env,
    );
    expect(res.status).toBe(404);
  });
});

describe("POST /admin/issues/:id/respond", () => {
  // Sem isso, o segundo teste re-espiona um env.EMAIL_QUEUE.send que o
  // primeiro teste já mockou e nunca restaurou — vi.spyOn devolve o MESMO
  // mock (com o histórico de chamadas do teste anterior já dentro).
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("grava a resposta e enfileira email pro reporter quando a issue tem email", async () => {
    const { token } = await createUser("admin");
    const id = await createIssue({ email: "reporter@example.com" });
    const sendSpy = vi
      .spyOn(env.EMAIL_QUEUE, "send")
      .mockImplementation(async () => undefined as never);

    const res = await admin.request(
      `/issues/${id}/respond`,
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          response: "Já corrigimos isso!",
          resolve: true,
        }),
      }),
      env,
    );
    expect(res.status).toBe(200);
    expect(sendSpy).toHaveBeenCalledTimes(1);

    const detail = await admin.request(`/issues/${id}`, authed(token), env);
    const body = await detail.json<{
      response: string | null;
      status: string;
    }>();
    expect(body.response).toBe("Já corrigimos isso!");
    expect(body.status).toBe("resolved");
  });

  it("grava a resposta SEM enfileirar email quando a issue não tem email (par de erro)", async () => {
    const { token } = await createUser("admin");
    const id = await createIssue({ email: null });
    const sendSpy = vi
      .spyOn(env.EMAIL_QUEUE, "send")
      .mockImplementation(async () => undefined as never);

    const res = await admin.request(
      `/issues/${id}/respond`,
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          response: "Resposta sem reporter",
          resolve: false,
        }),
      }),
      env,
    );
    expect(res.status).toBe(200);
    expect(sendSpy).not.toHaveBeenCalled();

    const detail = await admin.request(`/issues/${id}`, authed(token), env);
    const body = await detail.json<{ status: string }>();
    expect(body.status).toBe("open");
  });

  it("aceita somente uma resposta concorrente sem vínculo GitHub", async () => {
    const { token } = await createUser("admin");
    const id = await createIssue({ email: null });
    const request = (response: string) =>
      admin.request(
        `/issues/${id}/respond`,
        authed(token, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ response, resolve: false }),
        }),
        env,
      );

    const responses = await Promise.all([
      request("Primeira resposta"),
      request("Segunda resposta"),
    ]);
    expect(responses.map((response) => response.status).sort()).toEqual([
      200, 409,
    ]);
  });

  it("rejeita resposta vazia/curta (400) e id inexistente (404)", async () => {
    const { token } = await createUser("admin");
    const id = await createIssue();

    const tooShort = await admin.request(
      `/issues/${id}/respond`,
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ response: "ok" }),
      }),
      env,
    );
    expect(tooShort.status).toBe(400);

    const missing = await admin.request(
      `/issues/${crypto.randomUUID()}/respond`,
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ response: "Resposta válida" }),
      }),
      env,
    );
    expect(missing.status).toBe(404);
  });

  it("não substitui resposta enquanto a publicação externa mantém lease ativo", async () => {
    const { token } = await createUser("admin");
    const id = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO issues (id, title, category, description, response, response_version, github_repo, github_number, github_sync_state, response_sync_lease_until) VALUES (?, 'Corrida', 'bug', 'Descrição', 'Resposta em publicação', 1, ?, 9911, 'response_syncing', datetime('now', '+5 minutes'))",
    )
      .bind(id, "vectora-ltda/vectora-issues")
      .run();

    const res = await admin.request(
      `/issues/${id}/respond`,
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          response: "Resposta substituta",
          resolve: false,
        }),
      }),
      env,
    );

    expect(res.status).toBe(409);
    const row = await env.DB.prepare(
      "SELECT response, response_version, response_sync_lease_until FROM issues WHERE id = ?",
    )
      .bind(id)
      .first<{
        response: string;
        response_version: number;
        response_sync_lease_until: string;
      }>();
    expect(row?.response).toBe("Resposta em publicação");
    expect(row?.response_version).toBe(1);
    expect(row?.response_sync_lease_until).toBeTruthy();
  });

  it("reserva a issue antes da publicação externa e rejeita resposta sobreposta", async () => {
    const { token } = await createUser("admin");
    const id = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO issues (id, title, category, description, github_repo, github_number, github_url, github_sync_state) VALUES (?, 'Resposta concorrente', 'bug', 'Descrição', ?, 9912, ?, 'synced')",
    )
      .bind(
        id,
        "vectora-ltda/vectora-issues",
        "https://github.com/vectora-ltda/vectora-issues/issues/9912",
      )
      .run();

    let releaseCommentLookup!: (response: Response) => void;
    const commentLookup = new Promise<Response>((resolve) => {
      releaseCommentLookup = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        if (url.includes("/issues/9912/comments") && method === "GET") {
          return commentLookup;
        }
        if (url.includes("/issues/9912/comments") && method === "POST") {
          return new Response(JSON.stringify({ id: 1 }), { status: 201 });
        }
        return new Response("{}", { status: 200 });
      }),
    );
    const githubEnv = { ...env, GITHUB_TOKEN: "test-token" };

    const firstResponse = admin.request(
      `/issues/${id}/respond`,
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ response: "Primeira resposta", resolve: true }),
      }),
      githubEnv,
    );
    let reachedSyncing = false;
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const row = await env.DB.prepare(
        "SELECT github_sync_state FROM issues WHERE id = ?",
      )
        .bind(id)
        .first<{ github_sync_state: string }>();
      if (row?.github_sync_state === "response_syncing") {
        reachedSyncing = true;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 1));
    }
    expect(reachedSyncing).toBe(true);

    const secondResponse = await admin.request(
      `/issues/${id}/respond`,
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ response: "Segunda resposta", resolve: false }),
      }),
      githubEnv,
    );
    expect(secondResponse.status).toBe(409);

    releaseCommentLookup(new Response("[]", { status: 200 }));
    expect((await firstResponse).status).toBe(200);
  });

  it("aceita somente uma de duas respostas concorrentes na mesma versão", async () => {
    const { token } = await createUser("admin");
    const id = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO issues (id, title, category, description, github_repo, github_number, github_url, github_sync_state) VALUES (?, 'CAS', 'bug', 'Descrição', ?, 9913, ?, 'synced')",
    )
      .bind(
        id,
        "vectora-ltda/vectora-issues",
        "https://github.com/vectora-ltda/vectora-issues/issues/9913",
      )
      .run();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (
          url.includes("/issues/9913/comments") &&
          (init?.method ?? "GET") === "GET"
        ) {
          return new Response("[]", { status: 200 });
        }
        if (url.includes("/issues/9913/comments") && init?.method === "POST") {
          return new Response(JSON.stringify({ id: 2 }), { status: 201 });
        }
        return new Response("{}", { status: 200 });
      }),
    );
    const githubEnv = { ...env, GITHUB_TOKEN: "test-token" };
    const request = (response: string, resolve: boolean) =>
      admin.request(
        `/issues/${id}/respond`,
        authed(token, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ response, resolve }),
        }),
        githubEnv,
      );

    const results = await Promise.all([
      request("Resposta A", true),
      request("Resposta B", false),
    ]);
    expect(results.map((result) => result.status).sort()).toEqual([200, 409]);
  });

  it("abandona a publicação antiga quando o lease de resposta expira", async () => {
    const { token } = await createUser("admin");
    const id = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO issues (id, title, category, description, github_repo, github_number, github_url, github_sync_state) VALUES (?, 'Lease', 'bug', 'Descrição', ?, 9914, ?, 'synced')",
    )
      .bind(
        id,
        "vectora-ltda/vectora-issues",
        "https://github.com/vectora-ltda/vectora-issues/issues/9914",
      )
      .run();
    let releaseFirstLookup!: (response: Response) => void;
    const firstLookup = new Promise<Response>((resolve) => {
      releaseFirstLookup = resolve;
    });
    let commentLookups = 0;
    let commentPosts = 0;
    let issueCloses = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        if (url.includes("/issues/9914/comments") && method === "GET") {
          commentLookups += 1;
          if (commentLookups === 1) return firstLookup;
          return new Response("[]");
        }
        if (url.includes("/issues/9914/comments") && method === "POST") {
          commentPosts += 1;
          return new Response(JSON.stringify({ id: commentPosts }), {
            status: 201,
          });
        }
        if (method === "PATCH") issueCloses += 1;
        return new Response("{}", { status: 200 });
      }),
    );
    const githubEnv = { ...env, GITHUB_TOKEN: "test-token" };
    const request = (response: string) =>
      admin.request(
        `/issues/${id}/respond`,
        authed(token, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ response, resolve: true }),
        }),
        githubEnv,
      );

    const firstResponse = request("Resposta antiga");
    for (let attempt = 0; attempt < 100 && commentLookups < 1; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1));
    }
    expect(commentLookups).toBeGreaterThanOrEqual(1);
    await env.DB.prepare(
      "UPDATE issues SET response_sync_lease_until = datetime('now', '-1 minute') WHERE id = ?",
    )
      .bind(id)
      .run();
    const secondResponse = request("Resposta nova");
    expect(await secondResponse).toHaveProperty("status", 200);
    releaseFirstLookup(new Response("[]"));
    expect((await firstResponse).status).toBe(409);
    expect(commentPosts).toBe(1);
    expect(issueCloses).toBe(1);
  });
});

describe("POST /admin/issues/:id/archive", () => {
  it("arquiva, some da listagem admin, mas GET /admin/issues/:id continua acessível", async () => {
    const { token } = await createUser("admin");
    const id = await createIssue({ title: "Vai ser arquivada" });

    const archive = await admin.request(
      `/issues/${id}/archive`,
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archived: true }),
      }),
      env,
    );
    expect(archive.status).toBe(200);

    const list = await admin.request("/issues", authed(token), env);
    const { issues } = await list.json<{ issues: Array<{ id: string }> }>();
    expect(issues.some((i) => i.id === id)).toBe(false);

    const detail = await admin.request(`/issues/${id}`, authed(token), env);
    expect(detail.status).toBe(200);
    expect(
      (await detail.json<{ archived_at: string | null }>()).archived_at,
    ).not.toBeNull();
  });

  it("desarquiva e volta a aparecer na listagem (par de erro: id inexistente → 404)", async () => {
    const { token } = await createUser("admin");
    const id = await createIssue({ title: "Vai e volta" });

    await admin.request(
      `/issues/${id}/archive`,
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archived: true }),
      }),
      env,
    );
    const unarchive = await admin.request(
      `/issues/${id}/archive`,
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archived: false }),
      }),
      env,
    );
    expect(unarchive.status).toBe(200);

    const list = await admin.request("/issues", authed(token), env);
    const { issues } = await list.json<{ issues: Array<{ id: string }> }>();
    expect(issues.some((i) => i.id === id)).toBe(true);

    const missing = await admin.request(
      `/issues/${crypto.randomUUID()}/archive`,
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archived: true }),
      }),
      env,
    );
    expect(missing.status).toBe(404);
  });
});

import { env } from "cloudflare:test";
import { describe, expect, it, vi, afterEach } from "vitest";
import {
  issues,
  MAX_ISSUE_FILES,
  ISSUE_FILE_LIMITS,
  reconcileIssueComments,
} from "../../src/issues/routes";
import {
  promoteIssue,
  reconcilePendingPromotions,
} from "../../src/issues/promotion";

afterEach(() => {
  vi.unstubAllGlobals();
});

function mockResendFetch() {
  return vi.fn(async () => new Response(JSON.stringify({})));
}

function issueFormData(files: File[] = []) {
  const form = new FormData();
  form.set("title", "Crash com anexos");
  form.set("category", "bug");
  form.set("description", "Descrição com evidências");
  form.set("turnstileToken", "test-token");
  for (const file of files) form.append("files", file);
  return form;
}

async function signedWebhook(
  payload: Record<string, unknown>,
  delivery: string,
) {
  const secret = "webhook-test-secret";
  const body = JSON.stringify(payload);
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const digest = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(body),
  );
  const signature = Array.from(new Uint8Array(digest), (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
  return issues.request(
    "/github/webhook",
    {
      method: "POST",
      headers: {
        "x-hub-signature-256": `sha256=${signature}`,
        "x-github-delivery": delivery,
        "Content-Type": "application/json",
      },
      body,
    },
    { ...env, GITHUB_ISSUES_WEBHOOK_SECRET: secret },
  );
}

describe("POST /issues", () => {
  it("creates an issue and lists it publicly without exposing the reporter email", async () => {
    vi.stubGlobal("fetch", mockResendFetch());

    const res = await issues.request(
      "/",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: "Crash on startup",
          category: "bug",
          description: "It crashes",
          email: "reporter@example.com",
          turnstileToken: "test-token",
        }),
      },
      env,
    );
    expect(res.status).toBe(200);

    const list = await issues.request("/", {}, env);
    const body = await list.json<Array<Record<string, unknown>>>();
    expect(body.some((i) => i.title === "Crash on startup")).toBe(true);
    expect(body.every((i) => !("email" in i))).toBe(true);
  });

  it("rejects a too-short title, an invalid category, and a missing turnstile token", async () => {
    const base = { turnstileToken: "test-token" };
    const tooShort = await issues.request(
      "/",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...base, title: "ab", category: "bug" }),
      },
      env,
    );
    expect(tooShort.status).toBe(400);

    const badCategory = await issues.request(
      "/",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...base,
          title: "Valid title",
          category: "nope",
        }),
      },
      env,
    );
    expect(badCategory.status).toBe(400);

    const noTurnstile = await issues.request(
      "/",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "Valid title", category: "bug" }),
      },
      env,
    );
    expect(noTurnstile.status).toBe(400);
  });

  it("rejects a failed turnstile verification", async () => {
    const customEnv = { ...env, TURNSTILE_SECRET_KEY: "test-secret" };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ success: false }))),
    );

    const res = await issues.request(
      "/",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: "Valid title",
          category: "bug",
          turnstileToken: "bad-token",
        }),
      },
      customEnv as never,
    );
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "turnstile_failed" });
  });
});

describe("POST /issues/github/webhook", () => {
  it("preserva uma promoção pendente durante eventos comuns da issue", async () => {
    const issueId = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO issues (id, title, category, description, github_repo, github_number, github_url, github_sync_state, approved_by) VALUES (?, 'promoção', 'bug', 'body', ?, ?, ?, 'promotion_pending', 'admin')",
    )
      .bind(
        issueId,
        "vectora-ltda/vectora-issues",
        9880,
        "https://github.com/vectora-ltda/vectora-issues/issues/9880",
      )
      .run();
    const payload = {
      action: "edited",
      repository: { full_name: "vectora-ltda/vectora-issues" },
      issue: {
        number: 9880,
        title: "Promoção editada",
        body: "body atualizado",
        state: "open",
        html_url: "https://github.com/vectora-ltda/vectora-issues/issues/9880",
      },
    };
    expect((await signedWebhook(payload, "delivery-9880")).status).toBe(200);
    const row = await env.DB.prepare(
      "SELECT github_sync_state FROM issues WHERE id = ?",
    )
      .bind(issueId)
      .first<{ github_sync_state: string }>();
    expect(row?.github_sync_state).toBe("promotion_pending");
    await env.DB.prepare("DELETE FROM issues WHERE id = ?").bind(issueId).run();
  });

  it("rejects missing signatures and ignores replayed deliveries", async () => {
    const missing = await issues.request(
      "/github/webhook",
      { method: "POST", body: "{}" },
      { ...env, GITHUB_ISSUES_WEBHOOK_SECRET: "webhook-test-secret" },
    );
    expect(missing.status).toBe(400);

    const payload = {
      action: "opened",
      repository: { full_name: "vectora-ltda/vectora-issues" },
      issue: {
        number: 9876,
        title: "Webhook issue",
        body: "body",
        state: "open",
        html_url: "https://github.com/vectora-ltda/vectora-issues/issues/9876",
      },
    };
    expect((await signedWebhook(payload, "delivery-9876")).status).toBe(200);
    const replay = await signedWebhook(payload, "delivery-9876");
    expect(replay.status).toBe(200);
    expect((await replay.json<{ duplicate?: boolean }>()).duplicate).toBe(true);
  });

  it("mirrors edited and deleted comments without exposing reporter email", async () => {
    const base = {
      repository: { full_name: "vectora-ltda/vectora-issues" },
      issue: {
        number: 9877,
        title: "Comment issue",
        body: "body",
        state: "open",
        html_url: "https://github.com/vectora-ltda/vectora-issues/issues/9877",
      },
    };
    await signedWebhook({ ...base, action: "opened" }, "delivery-9877-open");
    await signedWebhook(
      {
        ...base,
        action: "created",
        comment: {
          id: 77,
          body: "first",
          html_url: "https://github.com/comment/77",
          created_at: new Date().toISOString(),
          user: { login: "reporter" },
        },
      },
      "delivery-9877-comment",
    );
    await signedWebhook(
      {
        ...base,
        action: "edited",
        comment: {
          id: 77,
          body: "edited",
          html_url: "https://github.com/comment/77",
          created_at: new Date().toISOString(),
          user: { login: "reporter" },
        },
      },
      "delivery-9877-edit",
    );
    await signedWebhook(
      {
        ...base,
        action: "deleted",
        comment: {
          id: 77,
          body: "edited",
          html_url: "https://github.com/comment/77",
          created_at: new Date().toISOString(),
          user: { login: "reporter" },
        },
      },
      "delivery-9877-delete",
    );
    const local = await env.DB.prepare(
      "SELECT id FROM issues WHERE github_number = ?",
    )
      .bind(9877)
      .first<{ id: string }>();
    const detail = await issues.request(`/${local?.id}`, {}, env);
    const detailBody = await detail.json<{
      comments: unknown[];
      email?: string;
    }>();
    expect(detailBody.comments).toHaveLength(0);
    expect("email" in detailBody).toBe(false);
  });

  it("retoma promoção pendente sem número da issue core", async () => {
    const issueId = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO issues (id, title, category, description, github_sync_state, approved_by) VALUES (?, 'bug pendente', 'bug', 'descrição', 'promotion_pending', 'admin')",
    )
      .bind(issueId)
      .run();
    const requests: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        requests.push(`${init?.method ?? "GET"} ${url}`);
        if (url.includes("search/issues")) {
          return new Response(JSON.stringify({ items: [] }), { status: 200 });
        }
        return new Response(
          JSON.stringify({
            number: 4321,
            title: "bug pendente",
            body: "body",
            state: "open",
            html_url: "https://github.com/vectora-ltda/vectora/issues/4321",
          }),
          { status: 201 },
        );
      }),
    );

    await reconcilePendingPromotions({
      ...env,
      GITHUB_TOKEN: "test-token",
    });

    const row = await env.DB.prepare(
      "SELECT core_number, github_sync_state FROM issues WHERE id = ?",
    )
      .bind(issueId)
      .first<{ core_number: number | null; github_sync_state: string }>();
    expect(row).toEqual({ core_number: 4321, github_sync_state: "promoted" });
    expect(requests.some((request) => request.startsWith("POST "))).toBe(true);
  });

  it("não toma uma reserva de promoção ainda dentro do lease", async () => {
    const issueId = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO issues (id, title, category, description, github_sync_state, github_sync_error, approved_at, approved_by) VALUES (?, 'lease', 'bug', 'descrição', 'promotion_pending', 'token-ativo', datetime('now'), 'admin')",
    )
      .bind(issueId)
      .run();
    await reconcilePendingPromotions({ ...env, GITHUB_TOKEN: "test-token" });
    const row = await env.DB.prepare(
      "SELECT github_sync_state, github_sync_error FROM issues WHERE id = ?",
    )
      .bind(issueId)
      .first<{ github_sync_state: string; github_sync_error: string }>();
    expect(row).toEqual({
      github_sync_state: "promotion_pending",
      github_sync_error: "token-ativo",
    });
  });

  it("retoma promoção após persistir o mapeamento core sem recriar a issue", async () => {
    const issueId = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO issues (id, title, category, description, github_repo, github_number, github_url, core_repo, core_number, core_url, github_sync_state, approved_by) VALUES (?, 'bug core', 'bug', 'descrição', ?, 77, ?, ?, 4321, ?, 'promotion_pending', 'admin')",
    )
      .bind(
        issueId,
        "vectora-ltda/vectora-issues",
        "https://github.com/vectora-ltda/vectora-issues/issues/77",
        "vectora-ltda/vectora",
        "https://github.com/vectora-ltda/vectora/issues/4321",
      )
      .run();
    const requests: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        requests.push(`${init?.method ?? "GET"} ${url}`);
        if (url.includes("comments")) {
          return new Response("[]", { status: 200 });
        }
        return new Response("{}", { status: 200 });
      }),
    );

    await reconcilePendingPromotions({
      ...env,
      GITHUB_TOKEN: "test-token",
    });

    const row = await env.DB.prepare(
      "SELECT core_number, github_sync_state FROM issues WHERE id = ?",
    )
      .bind(issueId)
      .first<{ core_number: number | null; github_sync_state: string }>();
    expect(row).toEqual({ core_number: 4321, github_sync_state: "promoted" });
    expect(
      requests.some(
        (request) => request.startsWith("POST ") && /\/issues$/.test(request),
      ),
    ).toBe(false);
  });

  it("mantém uma única reserva durante promoções concorrentes", async () => {
    const issueId = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO issues (id, title, category, description, github_repo, github_number, github_url, github_sync_state, approved_at, approved_by) VALUES (?, 'concorrente', 'bug', 'descrição', ?, 9881, ?, 'approval_error', datetime('now', '-1 hour'), 'admin')",
    )
      .bind(
        issueId,
        "vectora-ltda/vectora-issues",
        "https://github.com/vectora-ltda/vectora-issues/issues/9881",
      )
      .run();
    const requests: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        requests.push(`${init?.method ?? "GET"} ${url}`);
        if (url.includes("search/issues")) return new Response('{"items":[]}');
        if (url.includes("comments")) return new Response("[]");
        if (init?.method === "POST") {
          return new Response(
            JSON.stringify({
              number: 9882,
              html_url: "https://github.com/vectora-ltda/vectora/issues/9882",
            }),
            { status: 201 },
          );
        }
        return new Response("{}", { status: 200 });
      }),
    );
    const results = await Promise.allSettled([
      promoteIssue({ ...env, GITHUB_TOKEN: "test-token" }, issueId, "admin"),
      promoteIssue({ ...env, GITHUB_TOKEN: "test-token" }, issueId, "admin"),
    ]);
    expect(
      results.filter((result) => result.status === "fulfilled"),
    ).toHaveLength(1);
    expect(
      results.filter((result) => result.status === "rejected"),
    ).toHaveLength(1);
    const reservation = await env.DB.prepare(
      "SELECT approved_at FROM issues WHERE id = ?",
    )
      .bind(issueId)
      .first<{ approved_at: string }>();
    expect(reservation?.approved_at).not.toBeNull();
    expect(
      requests.filter(
        (request) =>
          request.startsWith("POST ") && request.includes("/comments"),
      ),
    ).toHaveLength(1);
  });

  it("abandona a promoção antiga quando o reconciliador substitui o lease", async () => {
    const issueId = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO issues (id, title, category, description, github_repo, github_number, github_url, github_sync_state, approved_at, approved_by) VALUES (?, 'lease expirado', 'bug', 'descrição', ?, 9883, ?, 'approval_error', datetime('now', '-1 hour'), 'admin')",
    )
      .bind(
        issueId,
        "vectora-ltda/vectora-issues",
        "https://github.com/vectora-ltda/vectora-issues/issues/9883",
      )
      .run();

    let releaseFirstComment!: (response: Response) => void;
    const firstComment = new Promise<Response>((resolve) => {
      releaseFirstComment = resolve;
    });
    let commentReads = 0;
    let commentPosts = 0;
    let issueCloses = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("search/issues")) {
          return new Response('{"items":[]}');
        }
        if (url.includes("/comments") && (init?.method ?? "GET") === "GET") {
          commentReads += 1;
          return commentReads === 1 ? firstComment : new Response("[]");
        }
        if (url.endsWith("/issues") && init?.method === "POST") {
          return new Response(
            JSON.stringify({
              number: 9884,
              html_url: "https://github.com/vectora-ltda/vectora/issues/9884",
            }),
            { status: 201 },
          );
        }
        if (url.includes("/comments") && init?.method === "POST") {
          commentPosts += 1;
          return new Response(
            JSON.stringify({
              id: 1234,
              body: "backlink",
              html_url: "https://github.com/comment/1234",
              created_at: new Date().toISOString(),
            }),
            { status: 201 },
          );
        }
        if (init?.method === "PATCH") {
          issueCloses += 1;
        }
        return new Response("{}", { status: 200 });
      }),
    );

    const oldPromotion = promoteIssue(
      { ...env, GITHUB_TOKEN: "test-token" },
      issueId,
      "admin",
    );
    for (let attempt = 0; attempt < 100 && commentReads === 0; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1));
    }
    expect(commentReads).toBe(1);

    await env.DB.prepare(
      "UPDATE issues SET approved_at = datetime('now', '-1 hour') WHERE id = ?",
    )
      .bind(issueId)
      .run();
    await reconcilePendingPromotions({ ...env, GITHUB_TOKEN: "test-token" });
    releaseFirstComment(new Response("[]"));
    const oldResult = await Promise.allSettled([oldPromotion]);

    expect(oldResult[0]?.status).toBe("rejected");
    expect(commentPosts).toBe(1);
    expect(issueCloses).toBe(1);
  });

  it("marca comentários ativos como removidos quando o GitHub retorna uma lista vazia", async () => {
    const issueId = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO issues (id, title, category, description, github_repo, github_number, github_url) VALUES (?, ?, 'bug', ?, ?, ?, ?)",
    )
      .bind(
        issueId,
        "Issue sem comentários remotos",
        "body",
        "vectora-ltda/vectora-issues",
        9999,
        "https://github.com/vectora-ltda/vectora-issues/issues/9999",
      )
      .run();
    await env.DB.prepare(
      "INSERT INTO issue_comments (id, issue_id, github_comment_id, author, body, html_url, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
    )
      .bind(
        crypto.randomUUID(),
        issueId,
        123,
        "reporter",
        "comentário removido",
        "https://github.com/comment/123",
        new Date().toISOString(),
      )
      .run();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("[]", { status: 200 })),
    );

    await reconcileIssueComments(
      { ...env, GITHUB_TOKEN: "test-token" },
      issueId,
      "vectora-ltda/vectora-issues",
      9999,
    );

    const comment = await env.DB.prepare(
      "SELECT deleted_at FROM issue_comments WHERE issue_id = ? AND github_comment_id = ?",
    )
      .bind(issueId, 123)
      .first<{ deleted_at: string | null }>();
    expect(comment?.deleted_at).not.toBeNull();
  });
});

describe("POST /issues — anexos (multipart)", () => {
  it("aceita print + vídeo, grava no R2 e lista as keys sem expor o email", async () => {
    vi.stubGlobal("fetch", mockResendFetch());

    const image = new File([new Uint8Array(1024)], "print.png", {
      type: "image/png",
    });
    const video = new File([new Uint8Array(2048)], "repro.mp4", {
      type: "video/mp4",
    });
    const res = await issues.request(
      "/",
      { method: "POST", body: issueFormData([image, video]) },
      env,
    );
    expect(res.status).toBe(200);

    const list = await issues.request("/", {}, env);
    const body =
      await list.json<
        Array<{ title: string; files: string[]; email?: string }>
      >();
    const created = body.find((i) => i.title === "Crash com anexos");
    expect(created?.files).toHaveLength(2);
    expect(created?.files[0]).toMatch(/^issues\//);
    expect(created && "email" in created).toBe(false);

    for (const key of created?.files ?? []) {
      const stored = await env.R2.get(key);
      expect(stored).not.toBeNull();
    }

    const served = await issues.request(`/files/${created?.files[0]}`, {}, env);
    expect(served.status).toBe(200);
    expect(served.headers.get("Content-Type")).toBe("image/png");
  });

  it("recusa tipo proibido (400), excesso de arquivos (400) e arquivo grande demais (413)", async () => {
    vi.stubGlobal("fetch", mockResendFetch());

    const exe = new File([new Uint8Array(16)], "virus.exe", {
      type: "application/x-msdownload",
    });
    const badType = await issues.request(
      "/",
      { method: "POST", body: issueFormData([exe]) },
      env,
    );
    expect(badType.status).toBe(400);
    expect(await badType.json()).toEqual({ error: "invalid_file_type" });

    const many = Array.from(
      { length: MAX_ISSUE_FILES + 1 },
      (_, i) =>
        new File([new Uint8Array(8)], `p${i}.png`, { type: "image/png" }),
    );
    const tooMany = await issues.request(
      "/",
      { method: "POST", body: issueFormData(many) },
      env,
    );
    expect(tooMany.status).toBe(400);
    expect(await tooMany.json()).toEqual({ error: "too_many_files" });

    const pngLimit = ISSUE_FILE_LIMITS["image/png"] ?? 0;
    expect(pngLimit).toBeGreaterThan(0);
    const huge = new File([new Uint8Array(pngLimit + 1)], "huge.png", {
      type: "image/png",
    });
    const tooBig = await issues.request(
      "/",
      { method: "POST", body: issueFormData([huge]) },
      env,
    );
    expect(tooBig.status).toBe(413);
    expect(await tooBig.json()).toEqual({ error: "file_too_large" });
  });

  it("GET /files com key inexistente → 404 (par de erro)", async () => {
    const res = await issues.request(
      "/files/issues/nao-existe/arquivo.png",
      {},
      env,
    );
    expect(res.status).toBe(404);
  });
});

describe("GET /issues/:id", () => {
  it("retorna a issue sem email quando existe, e 404 quando não existe", async () => {
    vi.stubGlobal("fetch", mockResendFetch());

    const create = await issues.request(
      "/",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: "Issue individual",
          category: "feedback",
          description: "Detalhe da issue",
          email: "reporter@example.com",
          turnstileToken: "test-token",
        }),
      },
      env,
    );
    expect(create.status).toBe(200);

    const list = await issues.request("/", {}, env);
    const listBody = await list.json<Array<{ id: string; title: string }>>();
    const created = listBody.find((i) => i.title === "Issue individual");
    expect(created).toBeTruthy();

    const found = await issues.request(`/${created!.id}`, {}, env);
    expect(found.status).toBe(200);
    const foundBody = await found.json<Record<string, unknown>>();
    expect(foundBody.title).toBe("Issue individual");
    expect(foundBody.status).toBe("open");
    expect("email" in foundBody).toBe(false);

    const missing = await issues.request(`/${crypto.randomUUID()}`, {}, env);
    expect(missing.status).toBe(404);
  });

  it("issue arquivada (soft-delete) vira 404 pro público e some da listagem", async () => {
    const id = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO issues (id, title, category, description, archived_at) VALUES (?, 'Arquivada', 'bug', 'x', datetime('now'))",
    )
      .bind(id)
      .run();

    const detail = await issues.request(`/${id}`, {}, env);
    expect(detail.status).toBe(404);

    const list = await issues.request("/", {}, env);
    const listBody = await list.json<Array<{ id: string }>>();
    expect(listBody.some((i) => i.id === id)).toBe(false);
  });
});

describe("POST /issues/waitlist", () => {
  it("is idempotent for a duplicate email", async () => {
    vi.stubGlobal("fetch", mockResendFetch());
    const emailAddr = `${crypto.randomUUID()}@example.com`;
    const body = { email: emailAddr, turnstileToken: "test-token" };
    const first = await issues.request(
      "/waitlist",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
      env,
    );
    expect(first.status).toBe(200);

    const second = await issues.request(
      "/waitlist",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
      env,
    );
    expect(second.status).toBe(200);

    const row = await env.DB.prepare(
      "SELECT COUNT(*) as count FROM waitlist WHERE email = ?",
    )
      .bind(emailAddr)
      .first<{ count: number }>();
    expect(row?.count).toBe(1);
  });

  it("rejects an invalid email and a missing turnstile token", async () => {
    const res = await issues.request(
      "/waitlist",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: "not-an-email",
          turnstileToken: "test-token",
        }),
      },
      env,
    );
    expect(res.status).toBe(400);

    const noTurnstile = await issues.request(
      "/waitlist",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: "a@b.com" }),
      },
      env,
    );
    expect(noTurnstile.status).toBe(400);
  });

  it("rejects a failed turnstile verification", async () => {
    const customEnv = { ...env, TURNSTILE_SECRET_KEY: "test-secret" };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ success: false }))),
    );

    const res = await issues.request(
      "/waitlist",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: "a@b.com", turnstileToken: "bad" }),
      },
      customEnv as never,
    );
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "turnstile_failed" });
  });
});

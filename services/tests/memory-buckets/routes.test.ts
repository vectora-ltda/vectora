import { env } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";
import {
  ragLibrary,
  processRagReindex,
  NO_STORAGE_PROVIDER_REASON,
} from "../../src/memory-buckets/routes";
import { createSession } from "../../src/auth/session";

async function createUser(role: "user" | "admin" = "user") {
  const userId = crypto.randomUUID();
  await env.DB.prepare(
    "INSERT INTO users (id, email, password_hash, full_name, role) VALUES (?, ?, ?, ?, ?)",
  )
    .bind(
      userId,
      `${userId}@example.com`,
      "pbkdf2$1$AA==$AA==",
      "Test User",
      role,
    )
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

async function makePackage(status: "ready" | "pending" | "failed" = "ready") {
  const id = crypto.randomUUID();
  await env.DB.prepare(
    "INSERT INTO rag_packages (id, name, source_lib, source_version, size_bytes, checksum, storage_url, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
  )
    .bind(
      id,
      "fastapi-docs",
      "fastapi",
      "0.115.0",
      2048,
      "def456",
      "https://storage.example.com/fastapi-docs.tar.gz",
      status,
    )
    .run();
  return id;
}

describe("GET /memory-buckets", () => {
  it("lists the catalog and redirects a known package to its storage URL, 404 for unknown", async () => {
    const id = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO rag_packages (id, name, source_lib, source_version, size_bytes, checksum, storage_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
    )
      .bind(
        id,
        "requests-docs",
        "requests",
        "2.31.0",
        1024,
        "abc123",
        "https://storage.example.com/requests-docs.tar.gz",
      )
      .run();

    const list = await ragLibrary.request("/", {}, env);
    expect(list.status).toBe(200);
    const body = await list.json<Array<{ id: string; name: string }>>();
    expect(body.some((p) => p.id === id)).toBe(true);

    const download = await ragLibrary.request(
      `/${id}/download`,
      { redirect: "manual" },
      env,
    );
    expect(download.status).toBe(302);
    expect(download.headers.get("Location")).toBe(
      "https://storage.example.com/requests-docs.tar.gz",
    );

    const missing = await ragLibrary.request("/unknown-id/download", {}, env);
    expect(missing.status).toBe(404);
  });

  it("inclui o nome do publisher para buckets publicados", async () => {
    const { userId } = await createUser("user");
    const id = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO rag_packages (id, name, source_lib, source_version, size_bytes, checksum, storage_url, publisher_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    )
      .bind(
        id,
        "published-bucket",
        "community",
        "1",
        10,
        "abc",
        "https://storage.example.com/published.tar.gz",
        userId,
      )
      .run();

    const response = await ragLibrary.request("/", {}, env);
    const body =
      await response.json<Array<{ id: string; publisher: string | null }>>();

    expect(body.find((packageRow) => packageRow.id === id)?.publisher).toBe(
      "Test User",
    );
  });
});

describe("GET /memory-buckets?q=", () => {
  it("filtra por nome/descrição", async () => {
    const id = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO rag_packages (id, name, description, source_lib, source_version, size_bytes, checksum, storage_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    )
      .bind(
        id,
        "godot-4.7-docs",
        "documentação da engine Godot",
        "godot",
        "4.7",
        1024,
        "abc",
        "https://storage.example.com/godot.tar.gz",
      )
      .run();
    await env.DB.prepare(
      "INSERT INTO rag_packages (id, name, description, source_lib, source_version, size_bytes, checksum, storage_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    )
      .bind(
        crypto.randomUUID(),
        "react-docs",
        "documentação do React",
        "react",
        "19",
        1024,
        "def",
        "https://storage.example.com/react.tar.gz",
      )
      .run();

    const res = await ragLibrary.request("/?q=godot", {}, env);
    const body = await res.json<Array<{ id: string; name: string }>>();

    expect(body).toHaveLength(1);
    expect(body[0]?.id).toBe(id);
  });
});

describe("POST /memory-buckets/:id/reindex", () => {
  it("marca status=pending e enfileira o job rag_reindex", async () => {
    const id = await makePackage("ready");
    const sendSpy = vi.spyOn(env.JOBS_QUEUE, "send");

    const res = await ragLibrary.request(
      `/${id}/reindex`,
      { method: "POST" },
      env,
    );
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true, status: "pending" });
    expect(sendSpy).toHaveBeenCalledExactlyOnceWith({
      type: "rag_reindex",
      packageId: id,
    });

    const row = await env.DB.prepare(
      "SELECT status, status_reason FROM rag_packages WHERE id = ?",
    )
      .bind(id)
      .first<{ status: string; status_reason: string | null }>();
    expect(row).toEqual({ status: "pending", status_reason: null });
  });

  it("404 para pacote inexistente", async () => {
    const res = await ragLibrary.request(
      "/unknown-id/reindex",
      { method: "POST" },
      env,
    );
    expect(res.status).toBe(404);
  });
});

describe("POST /memory-buckets/publish", () => {
  it("publica um bucket autenticado — grava R2 + linha D1 com publisher_id", async () => {
    const { userId, token } = await createUser("user");

    const form = new FormData();
    form.set("name", "Meu bucket de teste");
    form.set("description", "descrição de teste");
    form.set("embed_model", "text-embedding-3-small");
    form.set("license", "MIT");
    form.set("file", new File(["conteudo"], "bucket.lance"), "bucket.lance");

    const res = await ragLibrary.request(
      "/publish",
      authed(token, { method: "POST", body: form }),
      env,
    );

    expect(res.status).toBe(200);
    const body = await res.json<{
      ok: boolean;
      id: string;
      verified: boolean;
    }>();
    expect(body.ok).toBe(true);
    expect(body.verified).toBe(false);

    const row = await env.DB.prepare(
      "SELECT publisher_id, embed_model, verified FROM rag_packages WHERE id = ?",
    )
      .bind(body.id)
      .first<{ publisher_id: string; embed_model: string; verified: number }>();
    expect(row).toEqual({
      publisher_id: userId,
      embed_model: "text-embedding-3-small",
      verified: 0,
    });
  });

  it("rejeita publicação sem embed_model (400) — campo obrigatório pra publicação nova", async () => {
    const { token } = await createUser("user");

    const form = new FormData();
    form.set("name", "Sem embed model");
    form.set("file", new File(["x"], "b.lance"), "b.lance");

    const res = await ragLibrary.request(
      "/publish",
      authed(token, { method: "POST", body: form }),
      env,
    );

    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "embed_model_required" });
  });

  it("rejeita chamada sem sessão (401)", async () => {
    const form = new FormData();
    form.set("name", "x");
    form.set("embed_model", "m");
    form.set("file", new File(["x"], "b.lance"), "b.lance");

    const res = await ragLibrary.request(
      "/publish",
      { method: "POST", body: form },
      env,
    );

    expect(res.status).toBe(401);
  });
});

describe("PATCH /memory-buckets/admin/:id/verify", () => {
  it("seta verified=1 quando chamado por admin", async () => {
    const id = await makePackage("ready");
    const { token } = await createUser("admin");

    const res = await ragLibrary.request(
      `/admin/${id}/verify`,
      authed(token, { method: "PATCH" }),
      env,
    );

    expect(res.status).toBe(200);
    const row = await env.DB.prepare(
      "SELECT verified FROM rag_packages WHERE id = ?",
    )
      .bind(id)
      .first<{ verified: number }>();
    expect(row?.verified).toBe(1);
  });

  it("403 quando chamado por não-admin", async () => {
    const id = await makePackage("ready");
    const { token } = await createUser("user");

    const res = await ragLibrary.request(
      `/admin/${id}/verify`,
      authed(token, { method: "PATCH" }),
      env,
    );

    expect(res.status).toBe(403);
  });

  it("404 para pacote inexistente", async () => {
    const { token } = await createUser("admin");

    const res = await ragLibrary.request(
      "/admin/unknown-id/verify",
      authed(token, { method: "PATCH" }),
      env,
    );

    expect(res.status).toBe(404);
  });
});

describe("GET /:id/download increments downloads_count", () => {
  it("incrementa downloads_count a cada download bem-sucedido", async () => {
    const id = await makePackage("ready");

    await ragLibrary.request(`/${id}/download`, { redirect: "manual" }, env);
    await ragLibrary.request(`/${id}/download`, { redirect: "manual" }, env);

    const row = await env.DB.prepare(
      "SELECT downloads_count FROM rag_packages WHERE id = ?",
    )
      .bind(id)
      .first<{ downloads_count: number }>();
    expect(row?.downloads_count).toBe(2);
  });
});

describe("processRagReindex", () => {
  it("marca o pacote como failed com o motivo — sem provedor de storage configurado", async () => {
    const id = await makePackage("pending");

    await processRagReindex(env, id);

    const row = await env.DB.prepare(
      "SELECT status, status_reason FROM rag_packages WHERE id = ?",
    )
      .bind(id)
      .first<{ status: string; status_reason: string | null }>();
    expect(row).toEqual({
      status: "failed",
      status_reason: NO_STORAGE_PROVIDER_REASON,
    });
  });
});

describe("Versionamento — GET / e GET /:name/versions", () => {
  async function insertPackage(
    overrides: Partial<{
      id: string;
      name: string;
      package_name: string | null;
      version: string;
      source_version: string;
    }> = {},
  ) {
    const id = overrides.id ?? crypto.randomUUID();
    await env.DB.prepare(
      `INSERT INTO rag_packages
        (id, name, source_lib, source_version, package_name, version,
         size_bytes, checksum, storage_url)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    )
      .bind(
        id,
        overrides.name ?? "fastapi-docs",
        "fastapi",
        overrides.source_version ?? "0.115.0",
        overrides.package_name ?? null,
        overrides.version ?? "0.0.1",
        2048,
        "def456",
        "https://storage.example.com/fastapi.tar.gz",
      )
      .run();
    return id;
  }

  it("listagem retorna a versão mais recente de cada package_name (sem duplicar)", async () => {
    // Mesmo bucket, duas versões (v0.1.0 e v0.2.0) — a listagem deve
    // colapsar para 1 item, o mais recente.
    await insertPackage({
      name: "bucket-x",
      package_name: "bucket",
      version: "0.1.0",
    });
    await insertPackage({
      name: "bucket-x",
      package_name: "bucket",
      version: "0.2.0",
    });

    const list = await ragLibrary.request("/", {}, env);
    expect(list.status).toBe(200);
    const body = await list.json<Array<{ version: string }>>();
    const buckets = body.filter(
      (p) => (p as { package_name?: string }).package_name === "bucket",
    );
    expect(buckets.length).toBe(1);
    expect(buckets[0]?.version).toBe("0.2.0");
  });
});

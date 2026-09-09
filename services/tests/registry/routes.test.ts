import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { registry } from "../../src/registry/routes";
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

async function makeSkill(
  overrides: Partial<{
    id: string;
    name: string;
    description: string;
    source: string;
    category: string | null;
    catalogSource: string;
    downloadsCount: number;
    packageName: string | null;
    version: string;
  }> = {},
) {
  const id = overrides.id ?? crypto.randomUUID();
  await env.DB.prepare(
    `INSERT INTO skills_catalog
       (id, name, description, source, category, catalog_source, downloads_count, package_name, version)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  )
    .bind(
      id,
      overrides.name ?? "Test Skill",
      overrides.description ?? "descrição de teste",
      overrides.source ?? "https://github.com/example/skill",
      overrides.category ?? null,
      overrides.catalogSource ?? "curated",
      overrides.downloadsCount ?? 0,
      overrides.packageName ?? null,
      overrides.version ?? "0.0.1",
    )
    .run();
  return id;
}

describe("GET /registry/mcp", () => {
  it("returns the seeded mcp_catalog entries (D1 real, migrations/0001_schema.sql)", async () => {
    const res = await registry.request("/mcp", {}, env);
    expect(res.status).toBe(200);
    const body = await res.json<{ entries: Array<{ id: string }> }>();
    expect(body.entries.map((e) => e.id)).toContain("filesystem");
  });

  it("?q= filtra por nome/descrição", async () => {
    const res = await registry.request("/mcp?q=GitHub", {}, env);
    const body = await res.json<{ entries: Array<{ id: string }> }>();
    expect(body.entries.map((e) => e.id)).toEqual(["github"]);
  });

  it("?category= filtra por categoria exata", async () => {
    const res = await registry.request("/mcp?category=database", {}, env);
    const body = await res.json<{ entries: Array<{ id: string }> }>();
    expect(body.entries.map((e) => e.id)).toEqual(["postgres"]);
  });
});

describe("GET /registry/skills", () => {
  it("returns the four official seeded skills", async () => {
    const res = await registry.request("/skills", {}, env);
    expect(res.status).toBe(200);
    const body = await res.json<{ entries: Array<{ id: string }> }>();
    expect(body.entries.map((entry) => entry.id)).toEqual([
      "vectora-code-review",
      "vectora-adr",
      "vectora-rfc",
      "vectora-prd",
    ]);
  });

  it("?q= filtra por nome/descrição", async () => {
    await makeSkill({ name: "Godot Helper", description: "ajuda com Godot" });
    await makeSkill({ name: "Outra Skill", description: "nada a ver" });

    const res = await registry.request("/skills?q=Godot", {}, env);
    const body = await res.json<{ entries: Array<{ name: string }> }>();

    expect(body.entries).toHaveLength(1);
    expect(body.entries[0]?.name).toBe("Godot Helper");
  });

  it("?category= filtra por categoria exata", async () => {
    await makeSkill({ name: "A", category: "game-dev" });
    await makeSkill({ name: "B", category: "devops" });

    const res = await registry.request("/skills?category=game-dev", {}, env);
    const body = await res.json<{ entries: Array<{ name: string }> }>();

    expect(body.entries.map((e) => e.name)).toEqual(["A"]);
  });

  it("colapsa múltiplas versões do mesmo package_name na versão mais recente", async () => {
    await makeSkill({
      name: "Godot Helper v1",
      packageName: "godot-helper-collapse",
      version: "0.1.0",
    });
    await makeSkill({
      name: "Godot Helper v2",
      packageName: "godot-helper-collapse",
      version: "0.2.0",
    });

    const res = await registry.request("/skills", {}, env);
    const body = await res.json<{
      entries: Array<{ name: string; version: string; package_name: string }>;
    }>();
    const matching = body.entries.filter(
      (e) => e.package_name === "godot-helper-collapse",
    );

    expect(matching.length).toBe(1);
    expect(matching[0]?.version).toBe("0.2.0");
    expect(matching[0]?.name).toBe("Godot Helper v2");
  });

  it("skill sem package_name (legado) aparece sem colapsar", async () => {
    await makeSkill({ name: "Sem versionamento", packageName: null });

    const res = await registry.request("/skills", {}, env);
    const body = await res.json<{ entries: Array<{ name: string }> }>();

    expect(body.entries.map((e) => e.name)).toContain("Sem versionamento");
  });
});

describe("GET /registry/skills/:name/versions", () => {
  it("lista todas as versões de um package_name, mais recente primeiro", async () => {
    await makeSkill({ packageName: "godot-helper-versions", version: "0.1.0" });
    await makeSkill({
      packageName: "godot-helper-versions",
      version: "0.10.0",
    });
    await makeSkill({ packageName: "godot-helper-versions", version: "0.2.0" });

    const res = await registry.request(
      "/skills/godot-helper-versions/versions",
      {},
      env,
    );
    const body = await res.json<{ entries: Array<{ version: string }> }>();

    expect(body.entries.map((e) => e.version)).toEqual([
      "0.10.0",
      "0.2.0",
      "0.1.0",
    ]);
  });

  it("package_name sem nenhuma versão publicada devolve lista vazia, não erro", async () => {
    const res = await registry.request("/skills/nao-existe/versions", {}, env);

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ entries: [] });
  });
});

describe("GET /registry/extensions", () => {
  it("returns an empty entries array (fora de escopo — SDK de extensões não existe)", async () => {
    const res = await registry.request("/extensions", {}, env);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ entries: [] });
  });
});

describe("POST /registry/skills", () => {
  it("publica uma skill autenticada — grava community/publisher_id/verified=0", async () => {
    const { userId, token } = await createUser("user");

    const res = await registry.request(
      "/skills",
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: "Minha Skill",
          description: "faz coisas úteis",
          source: "https://github.com/bruno/minha-skill",
          category: "productivity",
          tags: ["cli", "automation"],
        }),
      }),
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
      "SELECT publisher_id, verified, catalog_source, category, tags FROM skills_catalog WHERE id = ?",
    )
      .bind(body.id)
      .first<{
        publisher_id: string;
        verified: number;
        catalog_source: string;
        category: string;
        tags: string;
      }>();
    expect(row?.publisher_id).toBe(userId);
    expect(row?.verified).toBe(0);
    expect(row?.catalog_source).toBe("community");
    expect(row?.category).toBe("productivity");
    expect(JSON.parse(row?.tags ?? "[]")).toEqual(["cli", "automation"]);
  });

  it("sem version/package_name explícitos, usa default 0.0.1 e package_name=name", async () => {
    const { token } = await createUser("user");

    const res = await registry.request(
      "/skills",
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: "Skill Sem Versao",
          description: "d",
          source: "https://github.com/bruno/skill-sem-versao",
        }),
      }),
      env,
    );

    const body = await res.json<{ version: string; package_name: string }>();
    expect(body.version).toBe("0.0.1");
    expect(body.package_name).toBe("Skill Sem Versao");
  });

  it("com version/package_name explícitos, publica a versão nova do mesmo pacote", async () => {
    const { token } = await createUser("user");

    const res = await registry.request(
      "/skills",
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: "Godot Helper v3",
          description: "d",
          source: "https://github.com/bruno/godot-helper",
          package_name: "godot-helper",
          version: "0.3.0",
        }),
      }),
      env,
    );

    const body = await res.json<{ version: string; package_name: string }>();
    expect(body.version).toBe("0.3.0");
    expect(body.package_name).toBe("godot-helper");
  });

  it("rejeita source que não é URL git válida (400)", async () => {
    const { token } = await createUser("user");

    const res = await registry.request(
      "/skills",
      authed(token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: "x",
          description: "y",
          source: "não é uma url",
        }),
      }),
      env,
    );

    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "invalid_source" });
  });

  it("rejeita chamada sem sessão (401)", async () => {
    const res = await registry.request(
      "/skills",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: "x",
          description: "y",
          source: "https://github.com/a/b",
        }),
      },
      env,
    );

    expect(res.status).toBe(401);
  });
});

describe("PATCH /registry/admin/skills/:id/verify", () => {
  it("seta verified=1 quando chamado por admin", async () => {
    const id = await makeSkill({ catalogSource: "community" });
    const { token } = await createUser("admin");

    const res = await registry.request(
      `/admin/skills/${id}/verify`,
      authed(token, { method: "PATCH" }),
      env,
    );

    expect(res.status).toBe(200);
    const row = await env.DB.prepare(
      "SELECT verified FROM skills_catalog WHERE id = ?",
    )
      .bind(id)
      .first<{ verified: number }>();
    expect(row?.verified).toBe(1);
  });

  it("403 quando chamado por não-admin", async () => {
    const id = await makeSkill();
    const { token } = await createUser("user");

    const res = await registry.request(
      `/admin/skills/${id}/verify`,
      authed(token, { method: "PATCH" }),
      env,
    );

    expect(res.status).toBe(403);
  });

  it("404 para skill inexistente", async () => {
    const { token } = await createUser("admin");

    const res = await registry.request(
      "/admin/skills/nao-existe/verify",
      authed(token, { method: "PATCH" }),
      env,
    );

    expect(res.status).toBe(404);
  });
});

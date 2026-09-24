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
  it("exposes only entries synchronized from the official registry", async () => {
    const res = await registry.request("/mcp", {}, env);
    expect(res.status).toBe(200);
    const body = await res.json<{ entries: Array<{ id: string }> }>();
    expect(body.entries).toEqual([]);
  });

  it("?q= filtra por nome/descrição", async () => {
    await env.DB.prepare(
      "UPDATE mcp_catalog SET catalog_source = 'official' WHERE id = 'github'",
    ).run();
    const res = await registry.request("/mcp?q=GitHub", {}, env);
    const body = await res.json<{ entries: Array<{ id: string }> }>();
    expect(body.entries.map((e) => e.id)).toEqual(["github"]);
  });

  it("?category= filtra por categoria exata", async () => {
    await env.DB.prepare(
      "UPDATE mcp_catalog SET catalog_source = 'official' WHERE id = 'postgres'",
    ).run();
    const res = await registry.request("/mcp?category=database", {}, env);
    const body = await res.json<{ entries: Array<{ id: string }> }>();
    expect(body.entries.map((e) => e.id)).toEqual(["postgres"]);
  });

  it("trata filtros vazios como ausência de filtro", async () => {
    await env.DB.prepare(
      "UPDATE mcp_catalog SET catalog_source = 'official', catalog_status = 'active' WHERE id = 'github'",
    ).run();
    const baseline = await registry.request("/mcp", {}, env);
    const res = await registry.request("/mcp?q=&category=", {}, env);
    expect(res.status).toBe(200);
    const filtered = await res.json<{ entries: unknown[] }>();
    const unfiltered = await baseline.json<{ entries: unknown[] }>();
    expect(unfiltered.entries).toEqual(
      expect.arrayContaining([expect.objectContaining({ id: "github" })]),
    );
    expect(filtered.entries).toEqual(unfiltered.entries);
  });
});

describe("GET /registry/skills", () => {
  it("starts without seeded skills", async () => {
    const res = await registry.request("/skills", {}, env);
    expect(res.status).toBe(200);
    const body = await res.json<{ entries: Array<{ id: string }> }>();
    expect(body.entries).toEqual([]);
  });

  it("expõe o autor da skill a partir do publisher_id", async () => {
    const { userId } = await createUser();
    await env.DB.prepare(
      `INSERT INTO skills_catalog
       (id, name, description, source, catalog_source, publisher_id)
       VALUES (?, ?, ?, ?, 'curated', ?)`,
    )
      .bind(
        "publisher-skill",
        "Publisher Skill",
        "skill publicada por usuário",
        "https://github.com/example/publisher-skill",
        userId,
      )
      .run();

    const res = await registry.request("/skills", {}, env);
    const body = await res.json<{
      entries: Array<{ id: string; publisher: string | null }>;
    }>();

    expect(
      body.entries.find((entry) => entry.id === "publisher-skill"),
    ).toMatchObject({ publisher: "Test User" });
  });

  it("não expõe o e-mail quando a skill não tem nome público", async () => {
    const userId = crypto.randomUUID();
    const email = `${userId}@example.com`;
    await env.DB.prepare(
      "INSERT INTO users (id, email, password_hash, full_name, role) VALUES (?, ?, ?, ?, ?)",
    )
      .bind(userId, email, "pbkdf2$1$AA==$AA==", "", "user")
      .run();
    await env.DB.prepare(
      `INSERT INTO skills_catalog
       (id, name, description, source, catalog_source, publisher_id)
       VALUES (?, ?, ?, ?, 'curated', ?)`,
    )
      .bind(
        "anonymous-skill",
        "Anonymous Skill",
        "skill sem nome público",
        "https://github.com/example/anonymous-skill",
        userId,
      )
      .run();

    const res = await registry.request("/skills", {}, env);
    const body = await res.text();

    expect(body).not.toContain(email);
    expect(JSON.parse(body)).toEqual(
      expect.objectContaining({
        entries: expect.arrayContaining([
          expect.objectContaining({ id: "anonymous-skill", publisher: null }),
        ]),
      }),
    );
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

  it("trata filtros vazios de skills como ausência de filtro", async () => {
    const visibleId = await makeSkill({
      id: "empty-filter-visible-skill",
      name: "Visible empty filter skill",
    });
    const baseline = await registry.request("/skills", {}, env);
    const res = await registry.request("/skills?q=&category=&tags=", {}, env);
    expect(res.status).toBe(200);
    const filtered = await res.json<{ entries: unknown[] }>();
    const unfiltered = await baseline.json<{ entries: unknown[] }>();
    expect(unfiltered.entries).toEqual(
      expect.arrayContaining([expect.objectContaining({ id: visibleId })]),
    );
    expect(filtered.entries).toEqual(unfiltered.entries);
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

  it("não expõe skills autorais legadas da Vectora", async () => {
    await makeSkill({
      id: "vectora-code-review",
      name: "Vectora Code Review",
      packageName: "@vectora/code-review",
    });
    await makeSkill({ name: "Community Skill", packageName: "community" });

    const res = await registry.request("/skills", {}, env);
    const body = await res.json<{ entries: Array<{ name: string }> }>();

    expect(body.entries.map((entry) => entry.name)).toContain(
      "Community Skill",
    );
    expect(body.entries.map((entry) => entry.name)).not.toContain(
      "Vectora Code Review",
    );
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

  it("não expõe versões do namespace autoral legado", async () => {
    await makeSkill({ packageName: "@vectora/adr", version: "1.0.0" });

    const res = await registry.request(
      "/skills/%40vectora%2Fadr/versions",
      {},
      env,
    );

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
  it("não oferece publicação pública de skills", async () => {
    const res = await registry.request(
      "/skills",
      { method: "POST", body: JSON.stringify({}) },
      env,
    );

    expect(res.status).toBe(404);
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

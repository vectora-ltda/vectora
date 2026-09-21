import { env } from "cloudflare:test";
import { describe, expect, it, vi, afterEach } from "vitest";
import {
  discoverMcp,
  discoverSkills,
  runDiscovery,
} from "../../src/registry/discovery";

afterEach(() => {
  vi.unstubAllGlobals();
});

function mcpRegistryResponse(
  servers: unknown[],
  metadata: { nextCursor?: string | null } = {},
) {
  return new Response(JSON.stringify({ servers, metadata }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function npmServer(id: string, name: string) {
  return {
    server: {
      name: id,
      title: name,
      description: `descrição de ${name}`,
      repository: { url: `https://github.com/example/${id}` },
      packages: [
        {
          registryType: "npm",
          transport: { type: "stdio" },
          identifier: `${id}-pkg`,
          environmentVariables: [{ name: "API_KEY", isRequired: true }],
        },
      ],
    },
    _meta: {
      "io.modelcontextprotocol.registry/publisher-provided": {
        github: {
          name_with_owner: `example/${id}`,
          preferred_image: "https://github.com/example/icon.png",
          stargazer_count: 42,
        },
      },
    },
  };
}

function packagedServer(
  id: string,
  registryType: "npm" | "pypi" | "oci",
  identifier: string,
) {
  return {
    server: {
      name: id,
      title: id,
      description: id,
      packages: [
        {
          registryType,
          transport: { type: "stdio" },
          identifier,
        },
      ],
    },
  };
}

describe("discoverMcp", () => {
  it("sincroniza o catálogo oficial e substitui entradas locais com o mesmo id", async () => {
    await env.DB.prepare(
      "INSERT INTO mcp_catalog (id, name, description, install_cmd, category, vectora_verified, catalog_source) VALUES (?, ?, ?, ?, ?, 1, 'curated')",
    )
      .bind("already-curated", "Já curado manualmente", "d", "npx x", "custom")
      .run();

    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        mcpRegistryResponse([
          npmServer("already-curated", "Descoberto"),
          npmServer("com.example/new-server", "Novo Server"),
        ]),
      ),
    );

    const count = await discoverMcp(env);

    expect(count).toBe(2);

    const curated = await env.DB.prepare(
      "SELECT name, catalog_source FROM mcp_catalog WHERE id = 'already-curated'",
    ).first<{ name: string; catalog_source: string }>();
    expect(curated).toEqual({
      name: "Descoberto",
      catalog_source: "official",
    });

    const discovered = await env.DB.prepare(
      "SELECT name, catalog_source, icon_url FROM mcp_catalog WHERE id = 'com.example/new-server'",
    ).first<{
      name: string;
      catalog_source: string;
      icon_url: string | null;
    }>();
    expect(discovered).toEqual({
      name: "Novo Server",
      catalog_source: "official",
      icon_url: "https://github.com/example/icon.png",
    });
  });

  it("erro/borda: falha de rede não lança e retorna 0 sem quebrar o cron", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("timeout");
      }),
    );

    await expect(discoverMcp(env)).resolves.toBe(0);
  });

  it("não promove snapshot parcial quando uma página posterior falha", async () => {
    await env.DB.prepare(
      "INSERT INTO mcp_catalog (id, name, description, install_cmd, category, catalog_source, snapshot_id) VALUES (?, ?, ?, ?, ?, 'github', ?)",
    )
      .bind("kept-old", "Old", "d", "npx old", "custom", "old-snapshot")
      .run();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        mcpRegistryResponse(
          Array.from({ length: 30 }, (_, i) =>
            npmServer(`page-one-${i}`, `Page ${i}`),
          ),
          { nextCursor: "page-two" },
        ),
      )
      .mockResolvedValueOnce(new Response("upstream failure", { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(discoverMcp(env)).resolves.toBe(0);
    const old = await env.DB.prepare(
      "SELECT catalog_status FROM mcp_catalog WHERE id = 'kept-old'",
    ).first<{ catalog_status: string }>();
    expect(old?.catalog_status).toBe("active");
  });

  it("marca como ausente uma entrada GitHub que saiu do snapshot completo", async () => {
    await env.DB.prepare(
      "INSERT INTO mcp_catalog (id, name, description, install_cmd, category, catalog_source, snapshot_id) VALUES (?, ?, ?, ?, ?, 'github', ?)",
    )
      .bind("gone-server", "Gone", "d", "npx gone", "custom", "old-snapshot")
      .run();

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => mcpRegistryResponse([npmServer("kept", "Kept")])),
    );

    await discoverMcp(env);
    const gone = await env.DB.prepare(
      "SELECT catalog_status FROM mcp_catalog WHERE id = 'gone-server'",
    ).first<{ catalog_status: string }>();
    expect(gone?.catalog_status).toBe("missing");
  });

  it("mapeia runtimes do registry oficial sem forçar Node", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        mcpRegistryResponse([
          packagedServer("python-server", "pypi", "python-mcp"),
          packagedServer("container-server", "oci", "ghcr.io/example/mcp:1"),
        ]),
      ),
    );

    await discoverMcp(env);

    const rows = await env.DB.prepare(
      "SELECT id, install_cmd FROM mcp_catalog WHERE id IN ('python-server', 'container-server') ORDER BY id",
    ).all<{ id: string; install_cmd: string }>();
    expect(rows.results).toEqual([
      {
        id: "container-server",
        install_cmd: "docker run --rm ghcr.io/example/mcp:1",
      },
      { id: "python-server", install_cmd: "uvx python-mcp" },
    ]);
  });

  it("respeita maxEntries e não grava o restante da página", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        mcpRegistryResponse([
          npmServer("limited-one", "One"),
          npmServer("limited-two", "Two"),
        ]),
      ),
    );

    await expect(discoverMcp(env, 1)).resolves.toBe(1);
    const rows = await env.DB.prepare(
      "SELECT id FROM mcp_catalog WHERE id LIKE 'limited-%'",
    ).all<{ id: string }>();
    expect(rows.results).toHaveLength(1);
  });

  it("respeita maxEntries quando uma entrada posterior interrompe a página", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        mcpRegistryResponse([
          npmServer("limited-error-one", "One"),
          npmServer("limited-error-two", "Two"),
          null,
        ]),
      ),
    );

    await expect(discoverMcp(env, 1)).resolves.toBe(1);
    const rows = await env.DB.prepare(
      "SELECT id FROM mcp_catalog WHERE id LIKE 'limited-error-%'",
    ).all<{ id: string }>();
    expect(rows.results).toEqual([{ id: "limited-error-one" }]);
  });
});

describe("discoverSkills", () => {
  it("sem GITHUB_TOKEN, fica desligado — retorna 0 sem chamar fetch", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const count = await discoverSkills({ ...env, GITHUB_TOKEN: undefined });

    expect(count).toBe(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("com GITHUB_TOKEN, busca repos com SKILL.md e insere sem sobrescrever curated", async () => {
    await env.DB.prepare(
      "INSERT INTO skills_catalog (id, name, description, source, catalog_source) VALUES (?, ?, ?, ?, 'curated')",
    )
      .bind(
        "example/curated-skill",
        "Skill curada",
        "d",
        "https://github.com/example/curated-skill",
      )
      .run();

    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              items: [
                {
                  repository: {
                    full_name: "example/curated-skill",
                    name: "curated-skill",
                    description: "descrição descoberta (não deve vencer)",
                    html_url: "https://github.com/example/curated-skill",
                  },
                },
                {
                  repository: {
                    full_name: "someone/pdf-skill",
                    name: "pdf-skill",
                    description: "Extrai texto de PDFs",
                    html_url: "https://github.com/someone/pdf-skill",
                  },
                },
              ],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
      ),
    );

    const count = await discoverSkills({
      ...env,
      GITHUB_TOKEN: "gh-test-token",
    });

    expect(count).toBe(2);

    const curated = await env.DB.prepare(
      "SELECT description, catalog_source FROM skills_catalog WHERE id = 'example/curated-skill'",
    ).first<{ description: string; catalog_source: string }>();
    expect(curated).toEqual({ description: "d", catalog_source: "curated" });

    const discovered = await env.DB.prepare(
      "SELECT name, catalog_source FROM skills_catalog WHERE id = 'someone/pdf-skill'",
    ).first<{ name: string; catalog_source: string }>();
    expect(discovered).toEqual({ name: "pdf-skill", catalog_source: "github" });
  });

  it("erro/borda: resposta não-ok do GitHub retorna 0 sem lançar", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("forbidden", { status: 403 })),
    );

    const count = await discoverSkills({
      ...env,
      GITHUB_TOKEN: "gh-test-token",
    });

    expect(count).toBe(0);
  });
});

describe("runDiscovery", () => {
  it("roda as duas fontes isoladas — uma falhar não impede a outra", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("registry.modelcontextprotocol.io")) {
          throw new Error("mcp registry fora do ar");
        }
        return new Response(JSON.stringify({ items: [] }), { status: 200 });
      }),
    );

    await expect(
      runDiscovery({ ...env, GITHUB_TOKEN: "gh-test-token" }),
    ).resolves.toBeUndefined();
  });
});

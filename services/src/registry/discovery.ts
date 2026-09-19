/**
 * Discovery automático dos catálogos de MCP e Skills — roda no cron do
 * Worker (`scheduled()`, `src/index.ts`), popula `mcp_catalog`/
 * `skills_catalog` (D1) além do seed manual de `migrations/0001_schema.sql`.
 *
 * MCP: pagina o registry oficial mantido pela comunidade MCP/Anthropic
 * (`registry.modelcontextprotocol.io`), público e sem autenticação — mesma
 * fonte que `backend/services/registry_client.py::fetch_official_mcp_
 * registry` já usa do lado do cliente Python, agora também persistida no
 * D1 pra não depender de paginação ao vivo em toda leitura.
 *
 * Skills: não existe hoje nenhum registry público equivalente (skills.sh,
 * cogitado inicialmente, exige um `VERCEL_OIDC_TOKEN` só emitido dentro do
 * runtime de deploy da própria Vercel — inacessível a um Worker de
 * terceiro, confirmado via `skills.sh/docs/api`). Reavaliado em 2026-08-15:
 * limitação continua real — a API segue exigindo OIDC federation do
 * projeto Vercel (nenhuma alternativa de API key documentada ainda; existe
 * pedido aberto da comunidade por chave própria, `vercel-labs/skills#1053`,
 * sem resolução). Alternativa: GitHub code search por `filename:SKILL.md`
 * (`GITHUB_TOKEN` opcional — sem ele, essa metade do discovery fica
 * desligada, não é erro).
 *
 * As duas fontes são isoladas uma da outra (falha em uma nunca impede a
 * outra) e o upsert nunca sobrescreve uma linha `catalog_source='curated'`
 * mesmo que o id colida — curadoria manual sempre vence.
 */

import type { Env } from "../gateway/types";

const OFFICIAL_MCP_REGISTRY_URL =
  "https://registry.modelcontextprotocol.io/v0.1/servers";
const GITHUB_CODE_SEARCH_URL = "https://api.github.com/search/code";

interface DiscoveredMcp {
  id: string;
  name: string;
  description: string;
  install_cmd: string;
  env_vars: string[];
  homepage: string;
  category: string;
}

interface McpPackage {
  registryType?: string;
  transport?: { type?: string };
  identifier?: string;
  environmentVariables?: { name?: string; isRequired?: boolean }[];
}

interface McpServerEntry {
  server?: {
    name?: string;
    title?: string;
    description?: string;
    repository?: { url?: string };
    packages?: McpPackage[];
  };
}

function npmStdioPackage(server: McpServerEntry["server"]): McpPackage | null {
  for (const pkg of server?.packages ?? []) {
    if (
      pkg.registryType === "npm" &&
      (pkg.transport?.type ?? "stdio") === "stdio"
    ) {
      return pkg;
    }
  }
  return null;
}

function toDiscoveredMcp(item: McpServerEntry): DiscoveredMcp | null {
  const server = item.server;
  const pkg = npmStdioPackage(server);
  if (!pkg?.identifier || !server?.name) return null;
  const envVars = (pkg.environmentVariables ?? [])
    .filter((ev) => ev.isRequired && ev.name)
    .map((ev) => ev.name as string);
  return {
    id: server.name,
    name: server.title || server.name.split("/").pop() || server.name,
    description: server.description ?? "",
    install_cmd: `npx -y ${pkg.identifier}`,
    env_vars: envVars,
    homepage: server.repository?.url ?? "",
    category: "community",
  };
}

/** Pagina registry.modelcontextprotocol.io e faz upsert em mcp_catalog. */
export async function discoverMcp(env: Env, maxEntries = 100): Promise<number> {
  const found = new Map<string, DiscoveredMcp>();
  try {
    let cursor: string | undefined;
    while (found.size < maxEntries) {
      const url = new URL(OFFICIAL_MCP_REGISTRY_URL);
      url.searchParams.set("version", "latest");
      url.searchParams.set("limit", "100");
      if (cursor) url.searchParams.set("cursor", cursor);
      const resp = await fetch(url.toString());
      if (!resp.ok) break;
      const data = (await resp.json()) as {
        servers?: McpServerEntry[];
        metadata?: { nextCursor?: string };
      };
      for (const item of data.servers ?? []) {
        const connector = toDiscoveredMcp(item);
        if (connector) found.set(connector.id, connector);
      }
      cursor = data.metadata?.nextCursor;
      if (!cursor || !data.servers?.length) break;
    }
  } catch {
    return 0;
  }

  let upserted = 0;
  for (const c of found.values()) {
    try {
      await env.DB.prepare(
        `INSERT INTO mcp_catalog
           (id, name, description, install_cmd, env_vars, homepage, category, vectora_verified, catalog_source)
         VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'official')
         ON CONFLICT(id) DO UPDATE SET
           name = excluded.name,
           description = excluded.description,
           install_cmd = excluded.install_cmd,
           env_vars = excluded.env_vars,
           homepage = excluded.homepage,
           category = excluded.category,
           updated_at = datetime('now')
         WHERE mcp_catalog.catalog_source != 'curated'`,
      )
        .bind(
          c.id,
          c.name,
          c.description,
          c.install_cmd,
          JSON.stringify(c.env_vars),
          c.homepage,
          c.category,
        )
        .run();
      upserted++;
    } catch {
      // isola falha por entrada — uma linha malformada não derruba as demais
    }
  }
  return upserted;
}

interface GithubCodeSearchItem {
  repository?: {
    full_name?: string;
    name?: string;
    description?: string | null;
    html_url?: string;
  };
}

/** Busca repos GitHub públicos com um SKILL.md e faz upsert em skills_catalog. */
export async function discoverSkills(
  env: Env,
  maxEntries = 50,
): Promise<number> {
  if (!env.GITHUB_TOKEN) {
    console.warn(
      "registry discovery: GITHUB_TOKEN ausente; skills de terceiros não foram descobertas",
    );
    return 0;
  }

  let items: GithubCodeSearchItem[] = [];
  try {
    const url = new URL(GITHUB_CODE_SEARCH_URL);
    url.searchParams.set("q", "filename:SKILL.md");
    url.searchParams.set("per_page", String(Math.min(maxEntries, 100)));
    const resp = await fetch(url.toString(), {
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "vectora-services-registry-discovery",
      },
    });
    if (!resp.ok) return 0;
    const data = (await resp.json()) as { items?: GithubCodeSearchItem[] };
    items = data.items ?? [];
  } catch {
    return 0;
  }

  const seen = new Map<string, GithubCodeSearchItem["repository"]>();
  for (const item of items) {
    const repo = item.repository;
    if (repo?.full_name && !seen.has(repo.full_name)) {
      seen.set(repo.full_name, repo);
    }
  }

  let upserted = 0;
  for (const repo of seen.values()) {
    if (!repo?.full_name) continue;
    const id = repo.full_name;
    const name = repo.name ?? id;
    const description = repo.description ?? "";
    const source = repo.html_url ?? `https://github.com/${id}`;
    try {
      await env.DB.prepare(
        `INSERT INTO skills_catalog
           (id, name, description, source, tags, vectora_verified, catalog_source)
         VALUES (?, ?, ?, ?, '[]', 0, 'github')
         ON CONFLICT(id) DO UPDATE SET
           name = excluded.name,
           description = excluded.description,
           source = excluded.source,
           updated_at = datetime('now')
         WHERE skills_catalog.catalog_source != 'curated'`,
      )
        .bind(id, name, description, source)
        .run();
      upserted++;
    } catch {
      // isola falha por entrada
    }
  }
  return upserted;
}

/** Roda as duas descobertas — isoladas, uma falhar não impede a outra. */
export async function runDiscovery(env: Env): Promise<void> {
  await Promise.allSettled([discoverMcp(env), discoverSkills(env)]);
}

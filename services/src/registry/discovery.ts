/**
 * Discovery automático dos catálogos de MCP e Skills — roda no cron do
 * Worker (`scheduled()`, `src/index.ts`), popula `mcp_catalog`/
 * `skills_catalog` (D1) além do seed manual de `migrations/0001_schema.sql`.
 *
 * MCP: pagina o catálogo oficial do GitHub MCP Registry
 * (`api.mcp.github.com`), público e ordenado por relevância. Nenhum seed
 * local ou agregador paralelo entra no catálogo público.
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

const GITHUB_MCP_REGISTRY_URL =
  "https://api.mcp.github.com/2025-09-15/v0/servers";
const GITHUB_CODE_SEARCH_URL = "https://api.github.com/search/code";

interface DiscoveredMcp {
  id: string;
  name: string;
  description: string;
  install_cmd: string;
  env_vars: string[];
  homepage: string;
  category: string;
  icon_url: string;
  publisher: string;
  publisher_url: string;
  stars_count: number;
  downloads_count: number;
  runtime_hint: string;
  package_identifier: string;
  transport: string;
  server_url: string;
}

interface McpPackage {
  registryType?: string;
  transport?: { type?: string };
  identifier?: string;
  environmentVariables?: { name?: string; isRequired?: boolean }[];
  runtimeHint?: string;
  runtime_hint?: string;
}

interface McpRepository {
  name?: string;
  url?: string;
  stargazer_count?: number;
  downloads_count?: number;
}

interface McpServerEntry {
  name?: string;
  title?: string;
  description?: string;
  repository?: McpRepository;
  packages?: McpPackage[];
  remotes?: { type?: string; url?: string }[];
  server?: {
    name?: string;
    title?: string;
    description?: string;
    repository?: McpRepository;
    packages?: McpPackage[];
    remotes?: { type?: string; url?: string }[];
    _meta?: McpServerEntry["_meta"];
  };
  _meta?: {
    "io.modelcontextprotocol.registry/publisher-provided"?: {
      github?: {
        display_name?: string;
        name_with_owner?: string;
        preferred_image?: string;
        stargazer_count?: number;
        downloads_count?: number;
      };
    };
  };
}

function toDiscoveredMcp(item: McpServerEntry): DiscoveredMcp | null {
  const server = item.server ?? item;
  if (!server?.name) return null;
  const serverName = server.name;
  const pkg = (server.packages ?? []).find((candidate) => {
    if (!candidate.identifier) return false;
    const transport = candidate.transport?.type ?? "stdio";
    if (transport !== "stdio") return false;
    const registryType = candidate.registryType?.toLowerCase();
    return (
      registryType === "npm" ||
      registryType === "pypi" ||
      registryType === "oci"
    );
  });
  const remote = server.remotes?.find(
    (candidate) => candidate.url && isValidRemoteUrl(candidate.url),
  );
  if (!pkg?.identifier && !remote?.url) return null;
  const envVars = (pkg?.environmentVariables ?? [])
    .filter((ev) => ev.isRequired && ev.name)
    .map((ev) => ev.name as string);
  const github = (item._meta ?? server._meta)?.[
    "io.modelcontextprotocol.registry/publisher-provided"
  ]?.github;
  const registryType = pkg?.registryType?.toLowerCase();
  const runtimeHint =
    pkg?.runtimeHint?.toLowerCase() ??
    pkg?.runtime_hint?.toLowerCase() ??
    registryType ??
    "npm";
  const installCmd = pkg?.identifier
    ? registryType === "pypi" || runtimeHint === "uvx"
      ? `uvx ${pkg.identifier}`
      : registryType === "oci" || runtimeHint === "docker"
        ? `docker run --rm ${pkg.identifier}`
        : registryType === "npm" && ["npm", "npx"].includes(runtimeHint)
          ? `npx -y ${pkg.identifier}`
          : ""
    : "";
  if (pkg?.identifier && !installCmd) return null;
  const owner = serverName.split("/", 1)[0] ?? "";
  const publisher: string =
    github?.name_with_owner ?? (serverName.includes("/") ? owner : "");
  const publisherOwner = publisher.split("/", 1)[0] ?? "";
  const iconUrl =
    github?.preferred_image ??
    (/^[A-Za-z0-9_.-]+$/.test(publisherOwner)
      ? `https://github.com/${publisherOwner}.png?size=96`
      : "");
  return {
    id: serverName,
    name:
      server.title ||
      github?.display_name ||
      server.repository?.name ||
      serverName.split("/").pop() ||
      serverName,
    description: server.description ?? "",
    install_cmd: installCmd,
    env_vars: envVars,
    homepage: server.repository?.url ?? "",
    category: "community",
    icon_url: iconUrl,
    publisher,
    publisher_url: publisher
      ? `https://github.com/${publisher}`
      : (server.repository?.url ?? ""),
    stars_count:
      server.repository?.stargazer_count ?? github?.stargazer_count ?? 0,
    downloads_count:
      server.repository?.downloads_count ?? github?.downloads_count ?? 0,
    runtime_hint: runtimeHint,
    package_identifier: pkg?.identifier ?? "",
    transport: remote?.type === "sse" ? "sse" : remote?.url ? "http" : "stdio",
    server_url: remote?.url ?? "",
  };
}

/**
 * Accept only a syntactically valid HTTP endpoint here. The worker does
 * not resolve arbitrary catalog hosts: SSRF enforcement belongs to the
 * connection boundary (`validate_url` in the API), where DNS resolution and
 * redirect checks are available. Keeping this stage structural avoids a
 * brittle, incomplete list of private IP literals in production code.
 */
function isValidRemoteUrl(value: string): boolean {
  try {
    const url = new URL(value);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      url.username ||
      url.password
    ) {
      return false;
    }
    return url.hostname.length > 0;
  } catch {
    return false;
  }
}

/** Pagina o catálogo GitHub MCP e faz upsert em mcp_catalog. */
export async function discoverMcp(
  env: Env,
  maxEntries = Number.POSITIVE_INFINITY,
): Promise<number> {
  const found = new Map<string, DiscoveredMcp>();
  const pageSize = 30;
  let complete = false;
  let cursor: string | undefined;
  const seenCursors = new Set<string>();
  try {
    while (found.size < maxEntries) {
      const url = new URL(GITHUB_MCP_REGISTRY_URL);
      url.searchParams.set("limit", String(pageSize));
      if (cursor) url.searchParams.set("cursor", cursor);
      const resp = await fetch(url.toString(), {
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) return 0;
      const data = (await resp.json()) as {
        servers?: McpServerEntry[];
        metadata?: { nextCursor?: string | null; total_pages?: number };
      };
      for (const item of data.servers ?? []) {
        const connector = toDiscoveredMcp(item);
        if (connector) found.set(connector.id, connector);
      }
      const nextCursor = data.metadata?.nextCursor ?? undefined;
      if (!nextCursor) {
        complete = true;
        break;
      }
      if (seenCursors.has(nextCursor)) {
        // A repeated cursor cannot make progress; preserve the old snapshot.
        break;
      }
      seenCursors.add(nextCursor);
      cursor = nextCursor;
    }
  } catch {
    return upsertMcpSnapshot(env, selectMcpEntries(found, maxEntries), false);
  }

  return upsertMcpSnapshot(env, selectMcpEntries(found, maxEntries), complete);
}

function selectMcpEntries(
  found: Map<string, DiscoveredMcp>,
  maxEntries: number,
): Map<string, DiscoveredMcp> {
  if (!Number.isFinite(maxEntries)) return found;
  return new Map([...found].slice(0, Math.max(0, maxEntries)));
}

async function upsertMcpSnapshot(
  env: Env,
  found: Map<string, DiscoveredMcp>,
  complete: boolean,
): Promise<number> {
  if (found.size === 0) return 0;

  const snapshotId = crypto.randomUUID();
  let upserted = 0;
  let writesComplete = true;
  for (const c of found.values()) {
    try {
      await env.DB.prepare(
        `INSERT INTO mcp_catalog
           (id, name, description, install_cmd, env_vars, homepage, category, icon_url, publisher, publisher_url, stars_count, downloads_count, runtime_hint, package_identifier, transport, server_url, vectora_verified, catalog_source, snapshot_id, last_seen_at, catalog_status)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'official', ?, datetime('now'), 'active')
         ON CONFLICT(id) DO UPDATE SET
           name = excluded.name,
           description = excluded.description,
           install_cmd = excluded.install_cmd,
           env_vars = excluded.env_vars,
           homepage = excluded.homepage,
           category = excluded.category,
           icon_url = excluded.icon_url,
           publisher = excluded.publisher,
           publisher_url = excluded.publisher_url,
           stars_count = excluded.stars_count,
           downloads_count = excluded.downloads_count,
           runtime_hint = excluded.runtime_hint,
           package_identifier = excluded.package_identifier,
           transport = excluded.transport,
           server_url = excluded.server_url,
           catalog_source = excluded.catalog_source,
           snapshot_id = excluded.snapshot_id,
           last_seen_at = excluded.last_seen_at,
           catalog_status = 'active',
           updated_at = datetime('now')
         `,
      )
        .bind(
          c.id,
          c.name,
          c.description,
          c.install_cmd,
          JSON.stringify(c.env_vars),
          c.homepage,
          c.category,
          c.icon_url,
          c.publisher,
          c.publisher_url,
          c.stars_count,
          c.downloads_count,
          c.runtime_hint,
          c.package_identifier,
          c.transport,
          c.server_url,
          snapshotId,
        )
        .run();
      upserted++;
    } catch {
      // isola falha por entrada — uma linha malformada não derruba as demais
      writesComplete = false;
    }
  }
  if (complete && writesComplete) {
    await env.DB.prepare(
      "UPDATE mcp_catalog SET catalog_status = 'missing' WHERE catalog_source IN ('official', 'github') AND COALESCE(snapshot_id, '') != ?",
    )
      .bind(snapshotId)
      .run();
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

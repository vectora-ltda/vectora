/**
 * Discovery automático dos catálogos de MCP e Skills — roda no cron do
 * Worker (`scheduled()`, `src/index.ts`), popula `mcp_catalog`/
 * `skills_catalog` (D1) além do seed manual de `migrations/0001_schema.sql`.
 *
 * MCP: pagina o catálogo oficial do MCP Registry
 * (`registry.modelcontextprotocol.io`), público e sem token. Nenhum seed
 * local ou agregador paralelo entra no catálogo público.
 *
 * Skills são descobertas por busca de código no GitHub quando `GITHUB_TOKEN`
 * está configurado; sem token, essa fonte fica desativada de forma segura.
 *
 * As duas fontes são isoladas uma da outra (falha em uma nunca impede a
 * outra) e o upsert nunca sobrescreve uma linha `catalog_source='curated'`
 * mesmo que o id colida — curadoria manual sempre vence.
 */

import type { Env } from "../gateway/types";

const OFFICIAL_MCP_REGISTRY_URL =
  "https://registry.modelcontextprotocol.io/v0.1/servers";
const GITHUB_CODE_SEARCH_URL = "https://api.github.com/search/code";

type SyncStatus = "ready" | "unavailable" | "disabled";

async function claimSyncRun(
  env: Env,
  source: "mcp" | "skills",
): Promise<string> {
  const token = crypto.randomUUID();
  await env.DB.prepare(
    `CREATE TABLE IF NOT EXISTS registry_sync_runs (
       source TEXT PRIMARY KEY,
       token TEXT NOT NULL,
       started_at TEXT NOT NULL DEFAULT (datetime('now'))
     )`,
  ).run();
  await env.DB.prepare(
    `INSERT INTO registry_sync_runs (source, token)
     VALUES (?, ?)
     ON CONFLICT(source) DO UPDATE SET token = excluded.token, started_at = datetime('now')`,
  )
    .bind(source, token)
    .run();
  return token;
}

async function isCurrentSyncRun(
  env: Env,
  source: "mcp" | "skills",
  token: string,
): Promise<boolean> {
  const row = await env.DB.prepare(
    "SELECT token FROM registry_sync_runs WHERE source = ?",
  )
    .bind(source)
    .first<{ token: string }>();
  return row?.token === token;
}

async function recordSyncState(
  env: Env,
  source: "mcp" | "skills",
  status: SyncStatus,
  error: string | null = null,
  token?: string,
): Promise<void> {
  try {
    if (token) {
      await env.DB.prepare(
        `INSERT INTO registry_sync_state
           (source, status, last_synced_at, last_error)
         SELECT ?, ?, CASE WHEN ? = 'ready' THEN datetime('now') ELSE NULL END, ?
         WHERE EXISTS (
             SELECT 1 FROM registry_sync_runs
             WHERE source = ? AND token = ?
         )
         ON CONFLICT(source) DO UPDATE SET
           status = excluded.status,
           last_synced_at = CASE WHEN excluded.status = 'ready' THEN excluded.last_synced_at ELSE registry_sync_state.last_synced_at END,
           last_error = excluded.last_error,
           updated_at = datetime('now')
         WHERE EXISTS (
           SELECT 1 FROM registry_sync_runs
           WHERE source = ? AND token = ?
         )`,
      )
        .bind(source, status, status, error, source, token, source, token)
        .run();
      return;
    }
    await env.DB.prepare(
      `INSERT INTO registry_sync_state (source, status, last_synced_at, last_error)
       VALUES (?, ?, CASE WHEN ? = 'ready' THEN datetime('now') ELSE NULL END, ?)
       ON CONFLICT(source) DO UPDATE SET
         status = excluded.status,
         last_synced_at = CASE WHEN excluded.status = 'ready' THEN excluded.last_synced_at ELSE registry_sync_state.last_synced_at END,
         last_error = excluded.last_error,
         updated_at = datetime('now')`,
    )
      .bind(source, status, status, error)
      .run();
  } catch (recordError) {
    console.error("registry discovery: não foi possível registrar estado", {
      operation: "sync_state",
      source,
      error:
        recordError instanceof Error
          ? recordError.message
          : String(recordError),
    });
  }
}

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
  registry_name?: string;
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
  if (!item || typeof item !== "object") return null;
  const server = item.server ?? item;
  if (!server?.name) return null;
  const serverName = server.name;
  const pkg = (server.packages ?? []).find((candidate) => {
    if (!candidate.identifier) return false;
    const transport = candidate.transport?.type ?? "stdio";
    if (transport !== "stdio") return false;
    const registryType = (
      candidate.registryType ?? candidate.registry_name
    )?.toLowerCase();
    const runtimeHint = (
      candidate.runtimeHint ?? candidate.runtime_hint
    )?.toLowerCase();
    return (
      registryType === "npm" ||
      registryType === "pypi" ||
      registryType === "oci" ||
      runtimeHint === "npm" ||
      runtimeHint === "npx" ||
      runtimeHint === "uvx" ||
      runtimeHint === "docker"
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
  const registryType = (pkg?.registryType ?? pkg?.registry_name)?.toLowerCase();
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
        : registryType === "npm" || ["npm", "npx"].includes(runtimeHint)
          ? `npx -y ${pkg.identifier}`
          : ""
    : "";
  if (pkg?.identifier && !installCmd) return null;
  const owner = serverName.split("/", 1)[0] ?? "";
  const publisher: string = github?.name_with_owner
    ? (github.name_with_owner.split("/", 1)[0] ?? "")
    : owner;
  const publisherOwner = publisher;
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

/** Pagina o catálogo oficial e faz upsert atômico em mcp_catalog. */
export async function discoverMcp(
  env: Env,
  maxEntries = Number.POSITIVE_INFINITY,
): Promise<number> {
  const runToken = await claimSyncRun(env, "mcp");
  const found = new Map<string, DiscoveredMcp>();
  const pageSize = 100;
  let complete = false;
  let cursor: string | undefined;
  const seenCursors = new Set<string>();
  try {
    while (found.size < maxEntries) {
      const url = new URL(OFFICIAL_MCP_REGISTRY_URL);
      url.searchParams.set("limit", String(pageSize));
      url.searchParams.set("version", "latest");
      if (cursor) url.searchParams.set("cursor", cursor);
      const resp = await fetch(url.toString(), {
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        await recordSyncState(
          env,
          "mcp",
          "unavailable",
          `HTTP ${resp.status}`,
          runToken,
        );
        console.error("registry discovery: resposta MCP não-OK", {
          operation: "mcp_discovery",
          status: resp.status,
          cursor: cursor ?? null,
        });
        return 0;
      }
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
        await recordSyncState(
          env,
          "mcp",
          "unavailable",
          "cursor repetido",
          runToken,
        );
        console.error("registry discovery: cursor MCP repetido", {
          operation: "mcp_discovery",
          cursor: nextCursor,
        });
        return 0;
      }
      seenCursors.add(nextCursor);
      cursor = nextCursor;
    }
  } catch (error) {
    await recordSyncState(
      env,
      "mcp",
      "unavailable",
      error instanceof Error ? error.message : String(error),
      runToken,
    );
    console.error("registry discovery: falha ao ler MCP", {
      operation: "mcp_discovery",
      error: error instanceof Error ? error.message : String(error),
    });
    return 0;
  }

  const count = await upsertMcpSnapshot(
    env,
    selectMcpEntries(found, maxEntries),
    complete,
    !Number.isFinite(maxEntries),
    runToken,
  );
  if (complete && count === found.size) {
    await recordSyncState(env, "mcp", "ready", null, runToken);
  } else {
    await recordSyncState(
      env,
      "mcp",
      "unavailable",
      "snapshot não promovido",
      runToken,
    );
  }
  return count;
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
  reconcile: boolean,
  runToken: string,
): Promise<number> {
  if (!(await isCurrentSyncRun(env, "mcp", runToken))) return 0;
  const snapshotId = crypto.randomUUID();
  const statements: D1PreparedStatement[] = [];
  try {
    for (const c of found.values()) {
      statements.push(
        env.DB.prepare(
          `INSERT INTO mcp_catalog
           (id, name, description, install_cmd, env_vars, homepage, category, icon_url, publisher, publisher_url, stars_count, downloads_count, runtime_hint, package_identifier, transport, server_url, vectora_verified, catalog_source, snapshot_id, last_seen_at, catalog_status, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'official', ?, datetime('now'), 'active', datetime('now'))
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
           snapshot_id = excluded.snapshot_id,
           last_seen_at = excluded.last_seen_at,
           catalog_status = 'active',
           updated_at = datetime('now')
           WHERE mcp_catalog.catalog_source != 'curated'`,
        ).bind(
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
        ),
      );
    }
    if (complete && reconcile) {
      statements.push(
        env.DB.prepare(
          "UPDATE mcp_catalog SET catalog_status = 'missing' WHERE catalog_source IN ('official', 'github') AND COALESCE(snapshot_id, '') != ?",
        ).bind(snapshotId),
      );
    }
    if (!(await isCurrentSyncRun(env, "mcp", runToken))) return 0;
    if (statements.length > 0) await env.DB.batch(statements);
    return found.size;
  } catch (error) {
    console.error("registry discovery: snapshot MCP não foi promovido", {
      operation: "mcp_discovery",
      error: error instanceof Error ? error.message : String(error),
    });
    return 0;
  }
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
  const runToken = await claimSyncRun(env, "skills");
  if (!env.GITHUB_TOKEN) {
    await recordSyncState(
      env,
      "skills",
      "disabled",
      "GITHUB_TOKEN ausente",
      runToken,
    );
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
    if (!resp.ok) {
      await recordSyncState(
        env,
        "skills",
        "unavailable",
        `HTTP ${resp.status}`,
        runToken,
      );
      return 0;
    }
    const data = (await resp.json()) as { items?: GithubCodeSearchItem[] };
    items = data.items ?? [];
  } catch (error) {
    await recordSyncState(
      env,
      "skills",
      "unavailable",
      error instanceof Error ? error.message : String(error),
      runToken,
    );
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
  let failed = 0;
  for (const repo of seen.values()) {
    if (!(await isCurrentSyncRun(env, "skills", runToken))) return upserted;
    if (!repo?.full_name) continue;
    const id = repo.full_name;
    const name = repo.name ?? id;
    const description = repo.description ?? "";
    const source = repo.html_url ?? `https://github.com/${id}`;
    try {
      const writeResult = await env.DB.prepare(
        `INSERT INTO skills_catalog
           (id, name, description, source, tags, vectora_verified, catalog_source)
         SELECT ?, ?, ?, ?, '[]', 0, 'github'
         WHERE EXISTS (
           SELECT 1 FROM registry_sync_runs
           WHERE source = 'skills' AND token = ?
         )
         ON CONFLICT(id) DO UPDATE SET
           name = excluded.name,
           description = excluded.description,
           source = excluded.source,
           updated_at = datetime('now')
         WHERE skills_catalog.catalog_source != 'curated'
           AND EXISTS (
             SELECT 1 FROM registry_sync_runs
             WHERE source = 'skills' AND token = ?
           )`,
      )
        .bind(id, name, description, source, runToken, runToken)
        .run();
      if ((writeResult.meta?.changes ?? 0) > 0) {
        upserted++;
      } else if (!(await isCurrentSyncRun(env, "skills", runToken))) {
        return upserted;
      }
    } catch (error) {
      failed++;
      const cause = error instanceof Error ? error.message : String(error);
      console.error("registry discovery: falha ao gravar skill", {
        operation: "skills_upsert",
        skillId: id,
        error: cause,
      });
    }
  }
  if (failed > 0) {
    await recordSyncState(
      env,
      "skills",
      "unavailable",
      `skills_sync_failed:${failed}`,
      runToken,
    );
  } else {
    await recordSyncState(env, "skills", "ready", null, runToken);
  }
  return upserted;
}

/** Roda as duas descobertas — isoladas, uma falhar não impede a outra. */
export async function runDiscovery(env: Env): Promise<void> {
  await Promise.allSettled([discoverMcp(env), discoverSkills(env)]);
}

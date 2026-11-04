/**
 * updates/ — distribuição de releases do desktop Vectora (dentro do vectora-services).
 *
 * Serve manifestos ``latest*.yml`` para o ``electron-updater`` do desktop
 * Vectora, com rollout faseado e quarantine automática por crash report, e a
 * rota pública de primeira instalação (`/download/...`, sem token).
 *
 * `scripts/release.ts` publica os binários assinados no R2 e regenera
 * `config` no KV.
 *
 * Sem CORS de propósito: `/updates/*` é consumido pelo electron-updater
 * (HTTP client nativo, não browser); `/download/*` é um link de navegação
 * de página inteira (clique direto), não um fetch/XHR cross-origin — CORS
 * não se aplica a nenhum dos dois casos.
 */

import { Hono } from "hono";
import type { Env } from "../gateway/types";
import { enqueueJob } from "../lib/queue";

interface ChannelConfig {
  version: string;
  rollout_percent: number;
  previous_stable?: string;
  // Versões retidas em R2 pro canal (mais antiga primeiro), gravado por
  // scripts/release.ts — o worker não lê, só preserva o formato do KV.
  history?: string[];
}

interface RuntimeConfig {
  channels: Record<string, ChannelConfig>;
  quarantined: string[];
  // "<channel>/<version>" → chaves R2 publicadas por essa versão, usado por
  // scripts/release.ts pra podar sem precisar listar o bucket.
  uploads?: Record<string, string[]>;
}

const app = new Hono<{ Bindings: Env }>();

/** Hash determinístico estável → bucket [0..99] para rollout faseado. */
export function rolloutBucket(token: string): number {
  let h = 0;
  for (const ch of token) {
    h = (h * 31 + ch.charCodeAt(0)) | 0;
  }
  return Math.abs(h) % 100;
}

async function getConfig(kv: KVNamespace): Promise<RuntimeConfig> {
  const raw = await kv.get("config");
  if (!raw) {
    return { channels: {}, quarantined: [] };
  }
  return JSON.parse(raw) as RuntimeConfig;
}

/** Decide qual versão servir para o client baseado em rollout. */
export function resolveVersion(
  config: RuntimeConfig,
  channel: string,
  token: string,
): string | null {
  const ch = config.channels[channel];
  if (!ch) return null;
  if (config.quarantined.includes(ch.version)) {
    // Versão quarentinada → fallback para previous_stable.
    return ch.previous_stable ?? null;
  }
  if (rolloutBucket(token) < ch.rollout_percent) {
    return ch.version;
  }
  return ch.previous_stable ?? null;
}

/** Busca o objeto exato da arquitetura pedida — NUNCA cai pra outra arch.
 * O manifesto `latest.yml` de uma arch lista filenames dessa MESMA arch
 * (`Vectora-{versão}-{os}-{arch}.{ext}`); servir o manifesto de uma arch
 * diferente sob o path da arch pedida faz o electron-updater montar uma URL
 * de download que mistura os dois (`/win/x64/.../Vectora-...-win-arm64.exe`)
 * — chave que nunca existe no R2, 404 na hora de baixar o instalador mesmo
 * o "update" tendo aparecido como disponível. Bug real reproduzido em
 * produção (0.1.11→0.1.13, Windows arm64) antes desta correção.*/
async function getExactArchObject(
  kv: R2Bucket,
  channel: string,
  os: string,
  arch: string,
  version: string,
  filename: string,
): Promise<R2ObjectBody | null> {
  return kv.get(`${channel}/${os}/${arch}/${version}/${filename}`);
}

// Sem token: Free não tem conta, checar/baixar atualização não pode depender
// de estar logado. Rollout/quarentena continuam controlando QUAL versão é
// servida — isso é sobre segurança de release, não sobre autenticação.
app.get("/updates/:channel/:os/:arch/latest.yml", async (c) => {
  const token = c.req.query("token") ?? c.req.header("X-Vectora-Token") ?? "";
  const { channel, os, arch } = c.req.param();
  const config = await getConfig(c.env.KV);
  const version = resolveVersion(config, channel, token);
  if (!version) {
    return c.text("no version available", 404);
  }
  // O manifesto YAML em si é pré-gerado pelo script de release e fica
  // em R2 em ``<channel>/<os>/<arch>/<version>/latest.yml``.
  const obj = await getExactArchObject(
    c.env.R2,
    channel,
    os,
    arch,
    version,
    "latest.yml",
  );
  if (!obj) return c.text("manifest missing", 404);
  return new Response(obj.body, {
    headers: {
      "Content-Type": "application/x-yaml",
      "Cache-Control": "public, max-age=60",
      "X-Vectora-Version": version,
    },
  });
});

app.get("/updates/:channel/:os/:arch/:version/:filename", async (c) => {
  const { channel, os, arch, version, filename } = c.req.param();
  const obj = await getExactArchObject(
    c.env.R2,
    channel,
    os,
    arch,
    version,
    filename,
  );
  if (!obj) return c.text("not found", 404);
  return new Response(obj.body, {
    headers: {
      "Content-Type":
        obj.httpMetadata?.contentType ?? "application/octet-stream",
      "Cache-Control": "public, max-age=3600",
      ETag: obj.httpEtag,
    },
  });
});

/** Nome do instalador conforme o artifactName do electron-builder. */
export function installerFilename(
  version: string,
  os: string,
  arch: string,
  ext: string,
): string {
  return `Vectora-${version}-${os}-${arch}.${ext}`;
}

/** Parseia "win-x64.exe" → {os, arch, ext}; null se o formato não bater. */
export function parseDownloadTarget(
  target: string,
): { os: string; arch: string; ext: string } | null {
  const m =
    /^([a-z0-9]+)-([a-z0-9]+)\.(pkg\.tar\.zst|tar\.gz|[a-z0-9]+)$/i.exec(
      target,
    );
  if (!m?.[1] || !m[2] || !m[3]) return null;
  return { os: m[1], arch: m[2], ext: m[3] };
}

// Download de primeira instalação — sem token (o visitante do site ainda não
// tem app nem conta) e sem participar do rollout gradual (esse é só pra quem
// já tem o app e está checando update; quem baixa pela primeira vez recebe a
// versão estável do canal direto, ignorando quarentena/rollout_percent).
// URL no formato /download/:channel/win-x64.exe (um segmento de arquivo, não
// /os/arch/ext separados).
app.get("/download/:channel/:target", async (c) => {
  const { channel, target } = c.req.param();
  const parsed = parseDownloadTarget(target);
  if (!parsed) return c.text("invalid target", 400);
  const { os, arch, ext } = parsed;
  const config = await getConfig(c.env.KV);
  const ch = config.channels[channel];
  if (!ch) return c.text("unknown channel", 404);
  const version = config.quarantined.includes(ch.version)
    ? (ch.previous_stable ?? null)
    : ch.version;
  if (!version) return c.text("no version available", 404);

  const filename = installerFilename(version, os, arch, ext);
  const key = `${channel}/${os}/${arch}/${version}/${filename}`;
  const obj = await c.env.R2.get(key);
  if (!obj) return c.text("not found", 404);
  return new Response(obj.body, {
    headers: {
      "Content-Type":
        obj.httpMetadata?.contentType ?? "application/octet-stream",
      "Content-Disposition": `attachment; filename="${filename}"`,
      "Cache-Control": "public, max-age=300",
      "X-Vectora-Version": version,
    },
  });
});

// Versão atual exposta pro site (Hero + página de Downloads) — mesma
// resolução do `/download/:channel/:target` (versão estável do canal,
// respeitando quarentena), mas sem servir binário: só o número. Sem token,
// mesma justificativa do `/download` — visitante do site ainda não tem
// conta nem app instalado.
app.get("/version/:channel", async (c) => {
  const { channel } = c.req.param();
  const config = await getConfig(c.env.KV);
  const ch = config.channels[channel];
  if (!ch) return c.text("unknown channel", 404);
  const version = config.quarantined.includes(ch.version)
    ? (ch.previous_stable ?? null)
    : ch.version;
  if (!version) return c.text("no version available", 404);
  return new Response(JSON.stringify({ version, channel }), {
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "public, max-age=60",
    },
  });
});

interface TelemetryBody {
  state: "started" | "completed" | "failed";
  version: string;
  os: string;
  arch: string;
}

/**
 * Lógica de verdade da telemetria de update (contagem + quarentena
 * automática) — roda dentro do consumer da fila `vectora-jobs`
 * (`max_concurrency = 1`, ver wrangler.toml), nunca direto na rota HTTP:
 * serializar no consumer evita duas instalações reportando ao mesmo tempo
 * se pisarem na contagem.
 */
export async function processUpdateTelemetry(
  env: Env,
  body: TelemetryBody,
): Promise<void> {
  const bucket = `telem:${body.version}:${body.state}`;
  const current = parseInt((await env.KV.get(bucket)) ?? "0", 10);
  await env.KV.put(bucket, String(current + 1), { expirationTtl: 3600 });

  if (body.state === "failed" && current + 1 >= 3) {
    // 3+ falhas na mesma versão dentro de 1h → quarentina automática.
    const config = await getConfig(env.KV);
    if (!config.quarantined.includes(body.version)) {
      config.quarantined.push(body.version);
      await env.KV.put("config", JSON.stringify(config));
    }
  }
}

app.post("/telemetry/update-result", async (c) => {
  const body = await c.req.json<TelemetryBody>();
  await enqueueJob(c.env, { type: "update_telemetry", ...body });
  return c.json({ ok: true });
});

export default app;

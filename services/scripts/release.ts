/**
 * Publica os instaladores de `vectora/frontend/dist-electron/` no R2 e atualiza
 * o canal de release no KV (`channels.<channel>.version`).
 *
 * Uso (depois de `scons release`, a partir de services/):
 *   pnpm release -- --channel=latest --version=0.1.1 [--dist=<path>]
 *
 * Sobe pra key `<channel>/<os>/<arch>/<version>/<filename>` (mesmo padrão que
 * `updates/worker.ts` já lê em GET /updates/... e GET /download/...). O
 * manifesto `latest*.yml` que o electron-builder gera junto vai pro mesmo
 * lugar, com o nome fixo `latest.yml` (é o que
 * `GET /updates/:channel/:os/:arch/latest.yml` espera).
 *
 * R2 via API S3 (multipart) — `wrangler r2 object put` recusa arquivos acima
 * de 300 MiB e instaladores passam disso (AppImage ~460 MiB). Requer
 * CLOUDFLARE_ACCOUNT_ID + R2_ACCESS_KEY_ID + R2_SECRET_ACCESS_KEY no env
 * (API token R2 criado no dashboard Cloudflare). O KV continua via wrangler
 * (não tem API S3 e os valores são pequenos), autenticado por
 * CLOUDFLARE_API_TOKEN.
 */
import { DeleteObjectCommand, S3Client } from "@aws-sdk/client-s3";
import { Upload } from "@aws-sdk/lib-storage";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  createReadStream,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { stringify } from "yaml";

const CONTENT_TYPES: Record<string, string> = {
  exe: "application/x-msdownload",
  msi: "application/x-msi",
  dmg: "application/x-apple-diskimage",
  AppImage: "application/x-executable",
  deb: "application/vnd.debian.binary-package",
  rpm: "application/x-rpm",
  yml: "application/x-yaml",
  blockmap: "application/octet-stream",
};

// `Vectora-0.1.1-win-x64.exe` → { version: "0.1.1", os: "win", arch: "x64", ext: "exe" }
// Manifests (`latest.yml`, `latest-mac.yml`, `latest-linux.yml`) não seguem
// esse padrão — tratados à parte via MANIFEST_OS abaixo.
export const INSTALLER_RE =
  /^Vectora-(?<version>[^-]+)-(?<os>win|mac|linux)-(?<arch>x64|x86_64|amd64|arm64|universal)\.(?<ext>exe|msi|dmg|AppImage|deb|rpm)$/;

/** Electron-builder uses several spellings for the x64 Linux architecture. */
export function normalizeInstallerArch(
  arch: string,
): "x64" | "arm64" | "universal" {
  if (arch === "x86_64" || arch === "amd64") return "x64";
  if (arch === "x64" || arch === "arm64" || arch === "universal") return arch;
  throw new Error(`Arquitetura de instalador não suportada: ${arch}`);
}

/** Todos os formatos de instalador aceitos pelo canal de distribuição. */
export const INSTALLER_EXTENSIONS = [
  "exe",
  "msi",
  "dmg",
  "AppImage",
  "deb",
  "rpm",
] as const;

export const MANIFEST_OS: Record<string, string> = {
  "latest.yml": "win",
  "latest-mac.yml": "mac",
  "latest-linux.yml": "linux",
};

// Arquiteturas reais publicadas por SO (electron-builder.yml). mac só builda
// arm64 (Intel descontinuado); win e linux buildam os dois numa job só.
export const MANIFEST_ARCHES: Record<string, string[]> = {
  win: ["x64", "arm64"],
  mac: ["arm64"],
  linux: ["x64", "arm64"],
};

export interface InstallerManifestFile {
  url: string;
  sha512: string;
  size: number;
}

export interface ArchManifest {
  version: string;
  files: InstallerManifestFile[];
  path: string;
  sha512: string;
  releaseDate: string;
}

export function sha512Base64(filePath: string): string {
  return createHash("sha512").update(readFileSync(filePath)).digest("base64");
}

/**
 * Monta o manifesto `latest.yml` de UMA arch a partir do instalador real
 * daquela combinação (os, arch) — nunca do `latest.yml` bruto gerado pelo
 * electron-builder, que ao buildar duas arches na mesma job (win, linux)
 * sobrescreve o arquivo a cada arch buildada e nunca contém as duas
 * variantes. Publicar o YAML cru sob os dois prefixos R2 faz uma das arches
 * apontar pro instalador da outra — daí a reconstrução por arch aqui.
 */
export interface InstallerEntry {
  filename: string;
  path: string;
  /**
   * Todos os instaladores disponíveis para esta combinação de SO/arquitetura.
   * `filename` é apenas o artefato usado no manifesto do electron-updater;
   * cada item desta lista continua sendo enviado ao R2 pelo loop de upload.
   */
  availableFiles?: string[];
}

/**
 * Acha o instalador real de uma combinação (os, arch), ou lança — nunca
 * deixa `main()` publicar um manifesto de arch sem instalador
 * correspondente no `dist/` (regressão do bug de manifesto cross-arch).
 */
export function resolveInstaller(
  installersByOsArch: Map<string, InstallerEntry>,
  os: string,
  arch: string,
  manifestFile: string,
): InstallerEntry {
  const installer = installersByOsArch.get(`${os}/${arch}`);
  if (!installer) {
    throw new Error(
      `Manifesto ${manifestFile} precisa de um instalador ${os}/${arch}, mas nenhum foi encontrado — recusando publicar um latest.yml sem instalador correspondente.`,
    );
  }
  return installer;
}

export function buildArchManifest(
  version: string,
  installerPath: string,
  installerFilename: string,
): ArchManifest {
  const sha512 = sha512Base64(installerPath);
  const size = statSync(installerPath).size;
  return {
    version,
    files: [{ url: installerFilename, sha512, size }],
    path: installerFilename,
    sha512,
    releaseDate: new Date().toISOString(),
  };
}

// Quantas versões ficam disponíveis em R2 por canal. A poda apaga pelas
// chaves que cada versão gravou no KV (`uploads` abaixo), não por listagem
// do bucket — a lista registrada é a fonte de verdade do que cada versão
// publicou. 3 dá margem pra quem já está baixando uma versão no meio de um
// up-release nunca ver o download sumir no meio do caminho.
export const RETENTION_COUNT = 3;

export function computeRetention(
  history: string[],
  newVersion: string,
  retain: number = RETENTION_COUNT,
): { retained: string[]; pruned: string[] } {
  const safeRetain = Math.max(retain, 1);
  const updated = [...history.filter((v) => v !== newVersion), newVersion];
  if (updated.length <= safeRetain) {
    return { retained: updated, pruned: [] };
  }
  return {
    retained: updated.slice(updated.length - safeRetain),
    pruned: updated.slice(0, updated.length - safeRetain),
  };
}

export function parseArgs(argv: string[]) {
  const args = Object.fromEntries(
    argv
      .filter((a) => a.startsWith("--"))
      .map((a) => a.slice(2).split("=") as [string, string]),
  );
  const channel = args.channel ?? "latest";
  const version = args.version;
  const dist =
    args.dist ??
    join(__dirname, "..", "..", "vectora", "frontend", "dist-electron");
  if (!version) {
    throw new Error("--version=X.Y.Z é obrigatório");
  }
  return { channel, version, dist };
}

export function extOf(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot === -1 ? "" : filename.slice(dot + 1);
}

export interface R2ClientConfig {
  region: "auto";
  endpoint: string;
  credentials: { accessKeyId: string; secretAccessKey: string };
}

export function r2ClientConfig(
  env: Record<string, string | undefined> = process.env,
): R2ClientConfig {
  const required = [
    "CLOUDFLARE_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
  ] as const;
  const missing = required.filter((name) => !env[name]);
  if (missing.length > 0) {
    throw new Error(
      `Credenciais R2 ausentes no env: ${missing.join(", ")} — ` +
        "crie um API token R2 (dashboard Cloudflare → R2 → Manage API Tokens) " +
        "e exporte as três variáveis.",
    );
  }
  return {
    region: "auto",
    endpoint: `https://${env.CLOUDFLARE_ACCOUNT_ID}.r2.cloudflarestorage.com`,
    credentials: {
      accessKeyId: env.R2_ACCESS_KEY_ID as string,
      secretAccessKey: env.R2_SECRET_ACCESS_KEY as string,
    },
  };
}

let s3Singleton: S3Client | null = null;

function s3Client(): S3Client {
  s3Singleton ??= new S3Client(r2ClientConfig());
  return s3Singleton;
}

async function uploadFile(
  bucket: string,
  key: string,
  filePath: string,
  contentType: string,
): Promise<void> {
  console.log(`↑ ${key}`);
  await new Upload({
    client: s3Client(),
    params: {
      Bucket: bucket,
      Key: key,
      Body: createReadStream(filePath),
      ContentType: contentType,
    },
  }).done();
}

async function uploadBuffer(
  bucket: string,
  key: string,
  body: Buffer,
  contentType: string,
): Promise<void> {
  console.log(`↑ ${key}`);
  await new Upload({
    client: s3Client(),
    params: { Bucket: bucket, Key: key, Body: body, ContentType: contentType },
  }).done();
}

async function deleteFile(bucket: string, key: string): Promise<void> {
  console.log(`✗ ${key}`);
  await s3Client().send(new DeleteObjectCommand({ Bucket: bucket, Key: key }));
}

interface StoredConfig {
  channels: Record<
    string,
    {
      version: string;
      rollout_percent: number;
      previous_stable?: string;
      history: string[];
    }
  >;
  quarantined: string[];
  // "<channel>/<version>" → chaves R2 publicadas por essa versão, pra poda
  // apagar com precisão sem precisar listar o bucket.
  uploads: Record<string, string[]>;
}

function readConfig(): StoredConfig {
  try {
    const raw = execFileSync(
      "npx",
      ["wrangler", "kv", "key", "get", "config", "--binding=KV", "--remote"],
      { encoding: "utf-8", env: process.env, shell: true },
    );
    const parsed = JSON.parse(raw) as Partial<StoredConfig>;
    return {
      channels: parsed.channels ?? {},
      quarantined: parsed.quarantined ?? [],
      uploads: parsed.uploads ?? {},
    };
  } catch {
    return { channels: {}, quarantined: [], uploads: {} };
  }
}

function writeConfig(config: StoredConfig) {
  // O JSON passa por arquivo (--path), não como argv direto: no Windows,
  // execFileSync com shell:true roda via cmd.exe, que engole as aspas duplas
  // de uma string grande no argumento — corrompe o JSON antes de chegar no
  // wrangler (config gravada sem nenhuma aspa, JSON.parse quebra no worker).
  const tmpFile = join(tmpdir(), `vectora-release-config-${Date.now()}.json`);
  writeFileSync(tmpFile, JSON.stringify(config));
  try {
    execFileSync(
      "npx",
      [
        "wrangler",
        "kv",
        "key",
        "put",
        "config",
        "--path",
        tmpFile,
        "--binding=KV",
        "--remote",
      ],
      { stdio: "inherit", env: process.env, shell: true },
    );
  } finally {
    rmSync(tmpFile, { force: true });
  }
}

/**
 * Grava a nova versão no canal, registra as chaves R2 que ela publicou e
 * poda versões além de RETENTION_COUNT, apagando as chaves gravadas na
 * publicação delas.
 */
async function publishVersionAndPrune(
  bucket: string,
  channel: string,
  version: string,
  uploadedKeys: string[],
): Promise<void> {
  const config = readConfig();
  const existing = config.channels[channel];
  const { retained, pruned } = computeRetention(
    existing?.history ?? [],
    version,
  );

  config.channels[channel] = {
    version,
    rollout_percent: existing?.rollout_percent ?? 100,
    ...(existing?.version ? { previous_stable: existing.version } : {}),
    history: retained,
  };
  config.uploads[`${channel}/${version}`] = uploadedKeys;

  for (const prunedVersion of pruned) {
    const prunedKey = `${channel}/${prunedVersion}`;
    for (const key of config.uploads[prunedKey] ?? []) {
      await deleteFile(bucket, key);
    }
    delete config.uploads[prunedKey];
  }

  writeConfig(config);
  if (pruned.length > 0) {
    console.log(
      `✓ versões podadas do canal "${channel}": ${pruned.join(", ")}`,
    );
  }
}

/**
 * Indexa os instaladores reais de `files` por `os/arch`, restrito à
 * `version` publicada — um instalador de release anterior sobrando em
 * `dist` (build incremental, limpeza incompleta) nunca vira o conteúdo do
 * manifesto desta versão. Usado ANTES de montar qualquer manifesto: cada
 * um é reconstruído a partir do instalador real da combinação (ver
 * `buildArchManifest`), nunca do `latest.yml` bruto do disco.
 */
export function indexInstallersByOsArch(
  files: string[],
  dist: string,
  version: string,
): Map<string, InstallerEntry> {
  const installersByOsArch = new Map<string, InstallerEntry>();
  for (const file of files) {
    const match = INSTALLER_RE.exec(file);
    if (!match?.groups) continue;
    const { version: installerVersion, os, arch: rawArch } = match.groups;
    if (installerVersion !== version) continue;
    const key = `${os}/${normalizeInstallerArch(rawArch)}`;
    const already = installersByOsArch.get(key);
    if (already) {
      // Uma combinação pode ter vários formatos (por exemplo, Linux x64 tem
      // AppImage, deb e rpm). O manifesto do electron-updater aceita um único
      // arquivo, mas isso não autoriza descartar os demais: eles são enviados
      // separadamente no loop abaixo e ficam disponíveis para /download.
      const availableFiles = [
        ...(already.availableFiles ?? [already.filename]),
        file,
      ]
        .filter((name, index, names) => names.indexOf(name) === index)
        .sort((left, right) => left.localeCompare(right));
      installersByOsArch.set(key, {
        filename: already.filename,
        path: already.path,
        availableFiles,
      });
      continue;
    }
    installersByOsArch.set(key, {
      filename: file,
      path: join(dist, file),
      availableFiles: [file],
    });
  }
  for (const [key, entry] of installersByOsArch) {
    const os = key.split("/", 1)[0] ?? "";
    const availableFiles = entry.availableFiles ?? [entry.filename];
    const filename = selectManifestInstaller(os, availableFiles);
    installersByOsArch.set(key, {
      filename,
      path: join(dist, filename),
      availableFiles,
    });
  }
  return installersByOsArch;
}

/**
 * Escolhe o único arquivo exigido pelo manifesto do electron-updater.
 * Isso não é uma seleção de publicação: todos os candidatos continuam sendo
 * publicados no R2. Os formatos nativos só servem ao download explícito do
 * usuário, enquanto o updater usa o formato que ele próprio entende.
 */
export function selectManifestInstaller(
  os: string,
  filenames: string[],
): string {
  const updaterExtension =
    os === "win" ? ".exe" : os === "mac" ? ".dmg" : ".AppImage";
  const filename = filenames
    .filter((name) => name.endsWith(updaterExtension))
    .sort((left, right) => left.localeCompare(right))[0];
  if (!filename) {
    throw new Error(
      `Nenhum instalador ${updaterExtension} disponível para ${os}`,
    );
  }
  return filename;
}

async function main(): Promise<void> {
  const { channel, version, dist } = parseArgs(process.argv.slice(2));
  const bucket = "vectora-r2";

  const files = readdirSync(dist).filter(
    (f) => !f.endsWith(".yml.tmp") && !f.startsWith("."),
  );

  const installersByOsArch = indexInstallersByOsArch(files, dist, version);

  const uploadedKeys: string[] = [];
  for (const file of files) {
    const manifestOs = MANIFEST_OS[file];
    if (manifestOs) {
      for (const arch of MANIFEST_ARCHES[manifestOs] ?? []) {
        const installer = resolveInstaller(
          installersByOsArch,
          manifestOs,
          arch,
          file,
        );
        const manifest = buildArchManifest(
          version,
          installer.path,
          installer.filename,
        );
        const key = `${channel}/${manifestOs}/${arch}/${version}/latest.yml`;
        await uploadBuffer(
          bucket,
          key,
          Buffer.from(stringify(manifest)),
          CONTENT_TYPES.yml,
        );
        uploadedKeys.push(key);
      }
      continue;
    }

    const match = INSTALLER_RE.exec(file);
    if (!match?.groups) continue; // blockmap e outros artefatos auxiliares — não distribuídos
    const { version: installerVersion, os, arch: rawArch } = match.groups;
    const arch = normalizeInstallerArch(rawArch);
    // Mesmo filtro de indexInstallersByOsArch — um instalador de release
    // anterior sobrando em dist/ não pode ser publicado sob a key da
    // versão atual só porque casa o regex. Cada formato válido entra em
    // uma key própria; nenhum .exe, .msi, .dmg, AppImage, .deb ou .rpm é
    // descartado por haver outro formato para a mesma arch.
    if (installerVersion !== version) continue;
    const key = `${channel}/${os}/${arch}/${version}/${file}`;
    const contentType =
      CONTENT_TYPES[extOf(file)] ?? "application/octet-stream";
    await uploadFile(bucket, key, join(dist, file), contentType);
    uploadedKeys.push(key);
  }

  if (uploadedKeys.length === 0) {
    throw new Error(`Nenhum instalador/manifesto reconhecido em ${dist}`);
  }

  await publishVersionAndPrune(bucket, channel, version, uploadedKeys);
  console.log(
    `✓ ${uploadedKeys.length} arquivo(s) publicados no canal "${channel}" v${version}`,
  );
}

if (require.main === module) {
  main().catch((err: unknown) => {
    console.error(err);
    process.exit(1);
  });
}

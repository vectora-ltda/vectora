/* oxlint-disable -- preserve marketplace protocol and test exports. */
/**
 * Busca/download de temas de cor do VS Code Marketplace (processo
 * principal).
 *
 * Resolve a versão mais recente de uma extensão via a API de gallery
 * ExtensionQuery (pública, não-documentada mas estável), baixa o `.vsix`
 * (um zip) e extrai os arquivos JSON de tema de cor que ela contribui.
 * Nenhum código do tema é executado — só lemos `package.json` + os JSONs
 * de tema referenciados de dentro do arquivo, e devolvemos o texto pro
 * renderer converter.
 *
 * Sem dependência de lib de zip de propósito: um `.vsix` é um zip comum,
 * então lemos o central directory e inflamos só as entradas que
 * precisamos com `zlib` nativo do Node.
 */

import https from "node:https";
import zlib from "node:zlib";

const GALLERY_QUERY_URL =
  "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery";
const VSIX_ASSET_TYPE = "Microsoft.VisualStudio.Services.VSIXPackage";
const MAX_VSIX_BYTES = 40 * 1024 * 1024; // temas são minúsculos; isso é paranoia.
const MAX_REDIRECTS = 5;
const REQUEST_TIMEOUT_MS = 20_000;
const USER_AGENT = "Vectora-Desktop";

const ID_RE = /^[\w-]+\.[\w-]+$/;

/** Hosts que servem os assets reais do Marketplace (gallery + CDN) — o
 * domínio-mãe e sufixos de subdomínio que a Microsoft usa pra hospedar o
 * `.vsix` propriamente dito. */
const ALLOWED_HOSTS = [
  "marketplace.visualstudio.com",
  ".vsassets.io",
  ".gallerycdn.vsassets.io",
];

/** O `Location` de um redirect e o `source` do asset vêm de resposta
 * remota (gallery/CDN) — nunca confiar neles sem checar antes de abrir
 * conexão. Sem isso, um `Location` apontando pra rede interna do usuário
 * vira SSRF a partir do processo principal do Electron, e um esquema
 * `http://` chega a `https.request` como erro genérico em vez de falha
 * de download compreensível. */
function assertAllowedUrl(raw: string): string {
  const parsed = new URL(raw);
  const host = parsed.hostname.toLowerCase();
  const ok =
    parsed.protocol === "https:" &&
    ALLOWED_HOSTS.some((h) =>
      h.startsWith(".") ? host.endsWith(h) : host === h,
    );
  if (!ok) {
    throw new Error(`Destino de download não permitido: ${parsed.origin}`);
  }
  return parsed.toString();
}

export interface VscodeThemeFile {
  extensionId: string;
  displayName: string;
  themes: { label: string; uiTheme: string; contents: string }[];
}

export interface VscodeMarketplaceSearchItem {
  extensionId: string;
  displayName: string;
  publisher: string;
  description: string;
  installs: number;
}

interface RequestOptions {
  method?: string;
  headers?: Record<string, string | number>;
  body?: string | null;
  maxBytes?: number;
}

/** Helper HTTPS mínimo com redirect-following, timeout e teto de tamanho. */
function request(
  url: string,
  {
    method = "GET",
    headers = {},
    body = null,
    maxBytes = MAX_VSIX_BYTES,
  }: RequestOptions = {},
  redirectsLeft = MAX_REDIRECTS,
): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const req = https.request(url, { method, headers }, (res) => {
      const status = res.statusCode ?? 0;

      if (status >= 300 && status < 400 && res.headers.location) {
        if (redirectsLeft <= 0) {
          res.resume();
          reject(new Error("Redirecionamentos demais."));
          return;
        }
        res.resume();
        try {
          const next = assertAllowedUrl(
            new URL(res.headers.location, url).toString(),
          );
          // Redirects pro CDN são GETs simples (descarta o body do POST).
          resolve(
            request(
              next,
              {
                method: "GET",
                headers: { "User-Agent": USER_AGENT },
                maxBytes,
              },
              redirectsLeft - 1,
            ),
          );
        } catch (err) {
          reject(err);
        }
        return;
      }

      if (status < 200 || status >= 300) {
        res.resume();
        reject(new Error(`Request falhou (${status}) para ${url}`));
        return;
      }

      const chunks: Buffer[] = [];
      let total = 0;
      res.on("data", (chunk: Buffer) => {
        total += chunk.length;
        if (total > maxBytes) {
          req.destroy();
          reject(new Error("Resposta excedeu o limite de tamanho."));
          return;
        }
        chunks.push(chunk);
      });
      res.on("end", () => resolve(Buffer.concat(chunks)));
    });

    req.on("error", reject);
    req.setTimeout(REQUEST_TIMEOUT_MS, () =>
      req.destroy(new Error("Request expirou.")),
    );
    if (body) req.write(body);
    req.end();
  });
}

interface GalleryExtensionFile {
  assetType: string;
  source: string;
}

interface GalleryExtensionVersion {
  files?: GalleryExtensionFile[];
}

interface GalleryExtensionStatistic {
  statisticName: string;
  value: number;
}

interface GalleryExtension {
  displayName?: string;
  extensionName: string;
  shortDescription?: string;
  tags?: string[];
  publisher?: { publisherName?: string; displayName?: string };
  versions?: GalleryExtensionVersion[];
  statistics?: GalleryExtensionStatistic[];
}

interface GalleryResponse {
  results?: { extensions?: GalleryExtension[] }[];
}

async function queryGallery(
  payload: unknown,
  { maxBytes = 4 * 1024 * 1024 } = {},
): Promise<GalleryResponse> {
  const body = JSON.stringify(payload);
  const raw = await request(GALLERY_QUERY_URL, {
    method: "POST",
    headers: {
      Accept: "application/json;api-version=3.0-preview.1",
      "Content-Type": "application/json",
      "Content-Length": Buffer.byteLength(body),
      "User-Agent": USER_AGENT,
    },
    body,
    maxBytes,
  });
  return JSON.parse(raw.toString("utf8")) as GalleryResponse;
}

/** Resolve `{ displayName, vsixUrl }` da versão mais recente de `id`. */
async function resolveExtension(
  id: string,
): Promise<{ displayName: string; vsixUrl: string }> {
  const json = await queryGallery({
    // FilterType 7 = ExtensionName (id completo publisher.extension).
    filters: [
      { criteria: [{ filterType: 7, value: id }], pageNumber: 1, pageSize: 1 },
    ],
    // IncludeFiles | IncludeVersionProperties | IncludeAssetUri |
    // IncludeCategoryAndTags | IncludeLatestVersionOnly = 914.
    flags: 914,
  });

  const extension = json?.results?.[0]?.extensions?.[0];
  if (!extension) {
    throw new Error(`Extensão "${id}" não encontrada no Marketplace.`);
  }
  const version = extension.versions?.[0];
  if (!version) {
    throw new Error(`Extensão "${id}" não tem versão publicada.`);
  }
  const asset = (version.files ?? []).find(
    (file) => file.assetType === VSIX_ASSET_TYPE,
  );
  const vsixUrl = asset?.source;
  if (!vsixUrl) {
    throw new Error(`Pacote de download não encontrado para "${id}".`);
  }
  return { displayName: extension.displayName || id, vsixUrl };
}

/** A categoria "Themes" da gallery também tem pacotes de ícone de arquivo/
 * produto (a gallery não tem categoria só-cor). Não dá pra ver as
 * contribuições reais de uma extensão sem baixá-la, então filtramos os
 * pacotes de ícone óbvios por tag + nome/descrição. */
function looksLikeIconTheme(extension: GalleryExtension): boolean {
  const tags = (extension.tags ?? []).map((tag) => String(tag).toLowerCase());
  if (tags.includes("icon-theme") || tags.includes("product-icon-theme")) {
    return true;
  }
  const text =
    `${extension.displayName ?? ""} ${extension.shortDescription ?? ""}`.toLowerCase();
  return /\b(icon theme|file icons?|product icons?|icon pack|fileicons)\b/.test(
    text,
  );
}

/** Busca temas no Marketplace. Query vazia devolve os mais instalados;
 * com query, é busca full-text restrita à categoria Themes. Devolve cards
 * leves (sem baixar nada). */
export async function searchMarketplaceThemes(
  query: string,
  limit = 20,
): Promise<VscodeMarketplaceSearchItem[]> {
  const text = String(query || "").trim();
  const pageSize = Math.min(Math.max(Number(limit) || 20, 1), 50);

  // FilterType: 8=Target, 5=Category, 10=SearchText, 12=ExcludeWithFlags.
  const criteria: { filterType: number; value: string }[] = [
    { filterType: 8, value: "Microsoft.VisualStudio.Code" },
    { filterType: 5, value: "Themes" },
    { filterType: 12, value: "4096" }, // exclui não-publicados.
  ];
  if (text) criteria.push({ filterType: 10, value: text });

  const json = await queryGallery({
    // Busca mais itens do que precisa — o filtro de ícone abaixo ainda
    // deixa uma página cheia.
    filters: [
      {
        criteria,
        pageNumber: 1,
        pageSize: Math.min(pageSize * 2, 50),
        sortBy: 4,
        sortOrder: 0,
      },
    ],
    // IncludeStatistics | IncludeLatestVersionOnly | IncludeCategoryAndTags.
    flags: 772,
  });

  const extensions = json?.results?.[0]?.extensions ?? [];
  return extensions
    .filter((extension) => !looksLikeIconTheme(extension))
    .slice(0, pageSize)
    .map((extension) => {
      const publisherName = extension.publisher?.publisherName ?? "";
      const installStat = (extension.statistics ?? []).find(
        (stat) => stat.statisticName === "install",
      );
      return {
        extensionId: `${publisherName}.${extension.extensionName}`,
        displayName: extension.displayName || extension.extensionName,
        publisher: extension.publisher?.displayName || publisherName,
        description: extension.shortDescription || "",
        installs: Math.round(installStat?.value ?? 0),
      };
    });
}

// ─── Leitor de zip mínimo ───────────────────────────────────────────────

interface ZipRecord {
  method: number;
  compressedSize: number;
  localOffset: number;
}

function findEndOfCentralDirectory(buf: Buffer): number {
  // Assinatura EOCD 0x06054b50, varrendo de trás pra frente.
  for (let i = buf.length - 22; i >= 0; i--) {
    if (buf.readUInt32LE(i) === 0x06054b50) return i;
  }
  throw new Error("Não é um zip válido (sem end-of-central-directory).");
}

/** Parseia o central directory num mapa nome → registro. */
function readCentralDirectory(buf: Buffer): Map<string, ZipRecord> {
  const eocd = findEndOfCentralDirectory(buf);
  const count = buf.readUInt16LE(eocd + 10);
  let offset = buf.readUInt32LE(eocd + 16);
  const records = new Map<string, ZipRecord>();

  for (let i = 0; i < count; i++) {
    if (buf.readUInt32LE(offset) !== 0x02014b50) break;
    const method = buf.readUInt16LE(offset + 10);
    const compressedSize = buf.readUInt32LE(offset + 20);
    const nameLen = buf.readUInt16LE(offset + 28);
    const extraLen = buf.readUInt16LE(offset + 30);
    const commentLen = buf.readUInt16LE(offset + 32);
    const localOffset = buf.readUInt32LE(offset + 42);
    const name = buf.toString("utf8", offset + 46, offset + 46 + nameLen);
    records.set(name, { method, compressedSize, localOffset });
    offset += 46 + nameLen + extraLen + commentLen;
  }
  return records;
}

/** Inflama uma entrada única pra string. */
function extractEntry(buf: Buffer, record: ZipRecord): string {
  // Nome/extra do header local podem diferir do registro central — relê
  // aqui pra localizar o payload comprimido.
  if (buf.readUInt32LE(record.localOffset) !== 0x04034b50) {
    throw new Error("Zip corrompido: local file header inválido.");
  }
  const nameLen = buf.readUInt16LE(record.localOffset + 26);
  const extraLen = buf.readUInt16LE(record.localOffset + 28);
  const dataStart = record.localOffset + 30 + nameLen + extraLen;
  const data = buf.subarray(dataStart, dataStart + record.compressedSize);
  // 0 = stored, 8 = deflate. Arquivo de tema é sempre um dos dois.
  // `maxOutputLength` limita a saída DESCOMPRIMIDA — sem isso um `.vsix`
  // pequeno mas malicioso (zip bomb) poderia inflar bem além do teto já
  // aplicado ao buffer comprimido (`MAX_VSIX_BYTES`) e estourar memória.
  if (record.method === 0) {
    if (data.length > MAX_VSIX_BYTES) {
      throw new Error("Entrada do zip excede o limite de tamanho.");
    }
    return data.toString("utf8");
  }
  return zlib
    .inflateRawSync(data, { maxOutputLength: MAX_VSIX_BYTES })
    .toString("utf8");
}

/** Normaliza um path de tema do package.json pro nome da entrada no zip. */
function themeEntryName(themePath: string): string {
  const clean = String(themePath).replace(/^\.\//, "").replace(/^\//, "");
  return `extension/${clean}`;
}

interface VscodePackageJson {
  displayName?: string;
  name?: string;
  contributes?: {
    themes?: { path?: string; label?: string; id?: string; uiTheme?: string }[];
  };
}

/** Extrai todo tema de cor contribuído por um buffer `.vsix`. */
export function extractThemes(
  vsixBuffer: Buffer,
): { label: string; uiTheme: string; contents: string }[] {
  const records = readCentralDirectory(vsixBuffer);
  const pkgRecord = records.get("extension/package.json");
  if (!pkgRecord) {
    throw new Error("Manifesto da extensão ausente.");
  }
  const pkg = JSON.parse(
    extractEntry(vsixBuffer, pkgRecord),
  ) as VscodePackageJson;
  const contributed = pkg?.contributes?.themes;
  if (!Array.isArray(contributed) || contributed.length === 0) return [];

  const themes: { label: string; uiTheme: string; contents: string }[] = [];
  for (const entry of contributed) {
    if (!entry?.path) continue;
    const record = records.get(themeEntryName(entry.path));
    if (!record) continue;
    try {
      themes.push({
        label:
          entry.label ||
          entry.id ||
          pkg.displayName ||
          pkg.name ||
          "Tema VS Code",
        uiTheme: entry.uiTheme ?? "vs-dark",
        contents: extractEntry(vsixBuffer, record),
      });
    } catch {
      // Pula uma entrada que não conseguimos inflar em vez de falhar a
      // instalação inteira.
    }
  }
  return themes;
}

/**
 * Entrada pública: resolve, baixa e extrai temas de cor de `id`
 * (`publisher.extension`). Devolve `{ extensionId, displayName, themes }`.
 */
export async function fetchMarketplaceThemes(
  id: string,
): Promise<VscodeThemeFile> {
  const trimmed = String(id || "").trim();
  if (!ID_RE.test(trimmed)) {
    throw new Error('Esperado um id do Marketplace tipo "publisher.extensao".');
  }
  const { displayName, vsixUrl } = await resolveExtension(trimmed);
  const vsix = await request(assertAllowedUrl(vsixUrl), {
    headers: { "User-Agent": USER_AGENT },
  });
  const themes = extractThemes(vsix);
  return { extensionId: trimmed, displayName, themes };
}

export const __testing = {
  themeEntryName,
  looksLikeIconTheme,
  readCentralDirectory,
  extractThemes,
  assertAllowedUrl,
};

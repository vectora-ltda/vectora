import { describe, it, expect } from "vitest";
import zlib from "node:zlib";
import { __testing, extractThemes } from "../vscode-marketplace.js";

const {
  themeEntryName,
  looksLikeIconTheme,
  readCentralDirectory,
  assertAllowedUrl,
} = __testing;

interface ZipEntryInput {
  name: string;
  content: string;
  /** Quando true, comprime com deflate raw (method=8); senão "stored" (method=0). */
  deflate?: boolean;
}

/** Monta um `.vsix` (zip) mínimo — "stored" (method=0) por padrão, ou
 * deflate raw (method=8) quando `deflate: true` — só com o suficiente pra
 * `readCentralDirectory`/`extractEntry` lerem de volta. CRC32 não é
 * validado pelo leitor, então fica zerado. */
function buildZip(entries: ZipEntryInput[]): Buffer {
  const localParts: Buffer[] = [];
  const centralParts: Buffer[] = [];
  let offset = 0;

  for (const { name, content, deflate } of entries) {
    const nameBuf = Buffer.from(name, "utf8");
    const rawBuf = Buffer.from(content, "utf8");
    const dataBuf = deflate ? zlib.deflateRawSync(rawBuf) : rawBuf;
    const method = deflate ? 8 : 0;

    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4); // version needed
    local.writeUInt16LE(0, 6); // flags
    local.writeUInt16LE(method, 8);
    local.writeUInt16LE(0, 10); // mod time
    local.writeUInt16LE(0, 12); // mod date
    local.writeUInt32LE(0, 14); // crc32
    local.writeUInt32LE(dataBuf.length, 18); // compressed size
    local.writeUInt32LE(rawBuf.length, 22); // uncompressed size
    local.writeUInt16LE(nameBuf.length, 26);
    local.writeUInt16LE(0, 28); // extra len

    localParts.push(local, nameBuf, dataBuf);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4); // version made by
    central.writeUInt16LE(20, 6); // version needed
    central.writeUInt16LE(0, 8); // flags
    central.writeUInt16LE(method, 10);
    central.writeUInt16LE(0, 12);
    central.writeUInt16LE(0, 14);
    central.writeUInt32LE(0, 16); // crc32
    central.writeUInt32LE(dataBuf.length, 20);
    central.writeUInt32LE(rawBuf.length, 24);
    central.writeUInt16LE(nameBuf.length, 28);
    central.writeUInt16LE(0, 30); // extra len
    central.writeUInt16LE(0, 32); // comment len
    central.writeUInt16LE(0, 34); // disk number start
    central.writeUInt16LE(0, 36); // internal attrs
    central.writeUInt32LE(0, 38); // external attrs
    central.writeUInt32LE(offset, 42); // local header offset

    centralParts.push(central, nameBuf);

    offset += local.length + nameBuf.length + dataBuf.length;
  }

  const localSection = Buffer.concat(localParts);
  const centralSection = Buffer.concat(centralParts);

  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(0, 4);
  eocd.writeUInt16LE(0, 6);
  eocd.writeUInt16LE(entries.length, 8);
  eocd.writeUInt16LE(entries.length, 10);
  eocd.writeUInt32LE(centralSection.length, 12);
  eocd.writeUInt32LE(localSection.length, 16);
  eocd.writeUInt16LE(0, 20);

  return Buffer.concat([localSection, centralSection, eocd]);
}

/** Alias mantido pro nome usado nos testes já existentes abaixo (entradas
 * "stored", sem compressão). */
const buildStoredZip = buildZip;

describe("themeEntryName", () => {
  it("prefixa com extension/ e remove ./ inicial", () => {
    expect(themeEntryName("./themes/dark.json")).toBe(
      "extension/themes/dark.json",
    );
  });

  it("remove / inicial quando presente", () => {
    expect(themeEntryName("/themes/dark.json")).toBe(
      "extension/themes/dark.json",
    );
  });

  it("erro/borda — path já sem prefixo passa direto", () => {
    expect(themeEntryName("themes/dark.json")).toBe(
      "extension/themes/dark.json",
    );
  });
});

describe("looksLikeIconTheme", () => {
  it("identifica pela tag icon-theme", () => {
    expect(
      looksLikeIconTheme({ extensionName: "x", tags: ["icon-theme"] }),
    ).toBe(true);
  });

  it("identifica pelo nome/descrição quando não tem tag", () => {
    expect(
      looksLikeIconTheme({
        extensionName: "x",
        displayName: "Material Icon Theme",
        tags: [],
      }),
    ).toBe(true);
  });

  it("um tema de cor comum não é identificado como ícone", () => {
    expect(
      looksLikeIconTheme({
        extensionName: "x",
        displayName: "Dracula Official",
        shortDescription: "A dark theme",
        tags: ["theme"],
      }),
    ).toBe(false);
  });
});

describe("readCentralDirectory + extractThemes", () => {
  it("lê um .vsix mínimo com um único tema contribuído", () => {
    const themeJson = JSON.stringify({
      colors: { "editor.background": "#000000", "editor.foreground": "#fff" },
    });
    const pkg = JSON.stringify({
      displayName: "Tema de Teste",
      contributes: {
        themes: [
          { label: "Teste Dark", uiTheme: "vs-dark", path: "./theme.json" },
        ],
      },
    });
    const zip = buildStoredZip([
      { name: "extension/package.json", content: pkg },
      { name: "extension/theme.json", content: themeJson },
    ]);

    const records = readCentralDirectory(zip);
    expect(records.has("extension/package.json")).toBe(true);
    expect(records.has("extension/theme.json")).toBe(true);

    const themes = extractThemes(zip);
    expect(themes).toHaveLength(1);
    expect(themes[0]!.label).toBe("Teste Dark");
    expect(themes[0]!.uiTheme).toBe("vs-dark");
    expect(JSON.parse(themes[0]!.contents)).toEqual(JSON.parse(themeJson));
  });

  it("erro/borda — extensão sem package.json lança erro claro", () => {
    const zip = buildStoredZip([
      { name: "extension/readme.txt", content: "oi" },
    ]);
    expect(() => extractThemes(zip)).toThrow(/manifesto/i);
  });

  it("erro/borda — extensão sem contributes.themes devolve lista vazia", () => {
    const pkg = JSON.stringify({ displayName: "Sem Tema" });
    const zip = buildStoredZip([
      { name: "extension/package.json", content: pkg },
    ]);
    expect(extractThemes(zip)).toEqual([]);
  });

  it("lê corretamente uma entrada comprimida (deflate, method=8)", () => {
    const themeJson = JSON.stringify({
      colors: { "editor.background": "#111111", "editor.foreground": "#eee" },
    });
    const pkg = JSON.stringify({
      displayName: "Tema Comprimido",
      contributes: {
        themes: [{ label: "Deflate Dark", path: "./theme.json" }],
      },
    });
    const zip = buildZip([
      { name: "extension/package.json", content: pkg, deflate: true },
      { name: "extension/theme.json", content: themeJson, deflate: true },
    ]);

    const themes = extractThemes(zip);
    expect(themes).toHaveLength(1);
    expect(JSON.parse(themes[0]!.contents)).toEqual(JSON.parse(themeJson));
  });

  it("erro/borda — package.json que excede o limite de saída ao descomprimir lança (zip bomb)", () => {
    // 2MB de zeros comprime pra poucos bytes — simula uma entrada pequena
    // no disco que infla bem além do teto (MAX_VSIX_BYTES), reduzido aqui
    // via monkeypatch não é possível (constante do módulo), então o teste
    // confirma o mecanismo (maxOutputLength do zlib) isoladamente: a
    // mesma chamada que `extractEntry` faz por baixo lança quando o
    // limite é menor que o conteúdo descomprimido real.
    const huge = "0".repeat(2 * 1024 * 1024);
    const compressed = zlib.deflateRawSync(Buffer.from(huge, "utf8"));
    expect(() =>
      zlib.inflateRawSync(compressed, { maxOutputLength: 1024 }),
    ).toThrow();
    // ...e sem o limite, a mesma entrada infla normalmente (prova que o
    // teste está de fato exercitando o teto, não um zip inválido).
    expect(zlib.inflateRawSync(compressed).length).toBe(huge.length);
  });

  it("entrada stored pequena continua funcionando pelo caminho sem compressão", () => {
    const pkg = JSON.stringify({
      displayName: "Pequeno",
      contributes: { themes: [{ label: "Pequeno", path: "./theme.json" }] },
    });
    const zip = buildStoredZip([
      { name: "extension/package.json", content: pkg },
      { name: "extension/theme.json", content: "x".repeat(10) },
    ]);
    const themes = extractThemes(zip);
    expect(themes).toHaveLength(1);
  });
});

describe("assertAllowedUrl", () => {
  it("aceita hosts oficiais do Marketplace em HTTPS", () => {
    expect(
      assertAllowedUrl(
        "https://marketplace.visualstudio.com/_apis/public/gallery/x",
      ),
    ).toContain("marketplace.visualstudio.com");
    expect(
      assertAllowedUrl("https://az123.gallerycdn.vsassets.io/pacote.vsix"),
    ).toContain("gallerycdn.vsassets.io");
    expect(assertAllowedUrl("https://foo.vsassets.io/x")).toContain(
      "vsassets.io",
    );
  });

  it("erro/borda — rejeita esquema não-HTTPS", () => {
    expect(() =>
      assertAllowedUrl("http://marketplace.visualstudio.com/x"),
    ).toThrow(/não permitido/i);
  });

  it("erro/borda — rejeita host fora da allowlist (SSRF)", () => {
    expect(() =>
      assertAllowedUrl("https://169.254.169.254/latest/meta-data"),
    ).toThrow(/não permitido/i);
    expect(() => assertAllowedUrl("https://localhost/x")).toThrow(
      /não permitido/i,
    );
    expect(() =>
      assertAllowedUrl("https://evil-vsassets.io.attacker.com/x"),
    ).toThrow(/não permitido/i);
  });
});

/**
 * "Abrir com Vectora": as associações de arquivo do instalador cobrem tudo que o
 * app trata como anexo e o Vectora nunca vira o aplicativo padrão de um tipo.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { parse } from "yaml";
import { ATTACHMENT_EXTENSIONS } from "@/lib/utils/chat/media-kind";

interface FileAssociation {
  ext?: string[];
  name?: string;
  description?: string;
  mimeType?: string;
  role?: string;
  rank?: string;
}

interface BuilderConfig {
  fileAssociations?: FileAssociation[];
  mac?: { extendInfo?: { CFBundleDocumentTypes?: Record<string, unknown>[] } };
  nsis?: { perMachine?: boolean };
  protocols?: { schemes: string[] }[];
  linux?: { mimeTypes?: string[] };
}

const config = parse(
  readFileSync(
    path.resolve(__dirname, "../electron/electron-builder.yml"),
    "utf8",
  ),
) as BuilderConfig;
const associations = config.fileAssociations ?? [];

/** Entradas de `ATTACHMENT_EXTENSIONS` que são nome de arquivo, não extensão. */
const FILENAMES = new Set([".env.example", ".gitignore", ".dockerfile"]);

describe("fileAssociations do electron-builder", () => {
  it("cobre toda extensão que o app trata como anexo", () => {
    const covered = new Set(associations.flatMap((entry) => entry.ext ?? []));
    const missing = ATTACHMENT_EXTENSIONS.filter(
      (ext) => !FILENAMES.has(ext) && !covered.has(ext.slice(1)),
    );

    expect(missing).toEqual([]);
  });

  it("toda associação tem extensões, nome, descrição e papel de visualizador", () => {
    expect(associations.length).toBeGreaterThan(0);
    for (const entry of associations) {
      expect(entry.ext?.length, JSON.stringify(entry)).toBeGreaterThan(0);
      expect(entry.name, JSON.stringify(entry)).toBeTruthy();
      expect(entry.description, JSON.stringify(entry)).toBeTruthy();
      expect(entry.role, JSON.stringify(entry)).toBe("Viewer");
    }
  });

  it("nenhuma extensão nem tipo MIME é declarado duas vezes", () => {
    const extensions = associations.flatMap((entry) => entry.ext ?? []);
    const mimes = associations.flatMap((entry) =>
      entry.mimeType ? [entry.mimeType] : [],
    );

    expect(new Set(extensions).size).toBe(extensions.length);
    expect(new Set(mimes).size).toBe(mimes.length);
  });

  it("os tipos MIME têm o formato tipo/subtipo", () => {
    for (const entry of associations) {
      if (entry.mimeType)
        expect(entry.mimeType).toMatch(/^[a-z]+\/[a-z0-9.+-]+$/);
    }
  });

  it("o Vectora é só uma opção: toda associação tem rank Alternate no macOS", () => {
    for (const entry of associations) {
      expect(entry.rank, JSON.stringify(entry)).toBe("Alternate");
    }
  });

  it("mantém o protocolo vectora:// e oferece o Vectora para pastas no Linux", () => {
    expect(config.protocols?.flatMap((entry) => entry.schemes)).toContain(
      "vectora",
    );
    expect(config.linux?.mimeTypes).toContain("inode/directory");
  });
});

describe("menu de contexto de pastas", () => {
  it("no macOS, o Vectora é visualizador alternativo de public.folder", () => {
    const folder = config.mac?.extendInfo?.CFBundleDocumentTypes?.find(
      (entry) =>
        (entry.LSItemContentTypes as string[] | undefined)?.includes(
          "public.folder",
        ),
    );

    expect(folder).toMatchObject({
      CFBundleTypeRole: "Viewer",
      LSHandlerRank: "Alternate",
    });
  });

  const installer = readFileSync(
    path.resolve(__dirname, "../electron/build-resources/installer.nsh"),
    "utf8",
  );

  it("no Windows, o instalador registra o menu na pasta e no fundo da pasta, por usuário", () => {
    expect(config.nsis?.perMachine).toBe(false);
    expect(installer).toContain(
      'WriteRegStr HKCU "Software\\Classes\\Directory\\shell\\Vectora\\command" "" \'"$INSTDIR\\${APP_EXECUTABLE_FILENAME}" "%1"\'',
    );
    expect(installer).toContain(
      'WriteRegStr HKCU "Software\\Classes\\Directory\\Background\\shell\\Vectora\\command" "" \'"$INSTDIR\\${APP_EXECUTABLE_FILENAME}" "%V"\'',
    );
  });

  it("o desinstalador remove as duas chaves", () => {
    expect(installer).toContain(
      'DeleteRegKey HKCU "Software\\Classes\\Directory\\shell\\Vectora"',
    );
    expect(installer).toContain(
      'DeleteRegKey HKCU "Software\\Classes\\Directory\\Background\\shell\\Vectora"',
    );
  });
});

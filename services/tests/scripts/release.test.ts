import { afterEach, beforeEach, describe, it, expect } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parse as parseYaml, stringify as stringifyYaml } from "yaml";
import {
  parseArgs,
  extOf,
  INSTALLER_RE,
  normalizeInstallerArch,
  MANIFEST_OS,
  MANIFEST_ARCHES,
  RETENTION_COUNT,
  computeRetention,
  r2ClientConfig,
  buildArchManifest,
  resolveInstaller,
  sha512Base64,
  indexInstallersByOsArch,
  selectManifestInstaller,
} from "../../scripts/release";

describe("parseArgs", () => {
  it("channel default é 'latest'", () => {
    const { channel } = parseArgs(["--version=0.1.1"]);
    expect(channel).toBe("latest");
  });

  it("lê version e channel explícitos", () => {
    const { channel, version } = parseArgs([
      "--channel=beta",
      "--version=0.2.0",
    ]);
    expect(channel).toBe("beta");
    expect(version).toBe("0.2.0");
  });

  it("--dist sobrescreve o default", () => {
    const { dist } = parseArgs(["--version=0.1.1", "--dist=/tmp/builds"]);
    expect(dist).toBe("/tmp/builds");
  });

  it("sem --version → lança erro (par de erro)", () => {
    expect(() => parseArgs([])).toThrow("--version=X.Y.Z é obrigatório");
  });
});

describe("extOf", () => {
  it("extrai a extensão", () => {
    expect(extOf("Vectora-0.1.0-win-x64.exe")).toBe("exe");
    expect(extOf("latest.yml")).toBe("yml");
  });

  it("sem extensão → string vazia (edge)", () => {
    expect(extOf("semextensao")).toBe("");
  });
});

describe("INSTALLER_RE", () => {
  it("reconhece o padrão real do electron-builder", () => {
    const match = INSTALLER_RE.exec("Vectora-0.1.0-win-x64.exe");
    expect(match?.groups).toEqual({
      version: "0.1.0",
      os: "win",
      arch: "x64",
      ext: "exe",
    });
  });

  it("não reconhece blockmap nem arquivos fora do padrão (par de erro)", () => {
    expect(INSTALLER_RE.exec("Vectora-0.1.0-win-x64.exe.blockmap")).toBeNull();
    expect(INSTALLER_RE.exec("random-file.txt")).toBeNull();
    expect(INSTALLER_RE.exec("latest.yml")).toBeNull();
  });

  it("aceita mac universal e linux arm64", () => {
    expect(
      INSTALLER_RE.exec("Vectora-1.0.0-mac-universal.dmg")?.groups?.arch,
    ).toBe("universal");
    expect(
      INSTALLER_RE.exec("Vectora-1.0.0-linux-arm64.AppImage")?.groups?.os,
    ).toBe("linux");
  });

  it("aceita e normaliza os nomes de arquitetura emitidos no Linux", () => {
    expect(normalizeInstallerArch("x86_64")).toBe("x64");
    expect(normalizeInstallerArch("amd64")).toBe("x64");
    expect(
      INSTALLER_RE.exec("Vectora-0.1.19-linux-x86_64.AppImage")?.groups?.arch,
    ).toBe("x86_64");
    expect(
      INSTALLER_RE.exec("Vectora-0.1.19-linux-amd64.deb")?.groups?.arch,
    ).toBe("amd64");
  });

  it("aceita todos os formatos publicados pela página de downloads", () => {
    for (const extension of ["exe", "msi", "dmg", "AppImage", "deb", "rpm"]) {
      expect(
        INSTALLER_RE.exec(
          `Vectora-1.0.0-${extension === "dmg" ? "mac" : extension === "AppImage" || extension === "deb" || extension === "rpm" ? "linux" : "win"}-x64.${extension}`,
        )?.groups?.ext,
      ).toBe(extension);
    }
  });
});

describe("MANIFEST_OS", () => {
  it("mapeia os 3 nomes de manifesto do electron-builder", () => {
    expect(MANIFEST_OS["latest.yml"]).toBe("win");
    expect(MANIFEST_OS["latest-mac.yml"]).toBe("mac");
    expect(MANIFEST_OS["latest-linux.yml"]).toBe("linux");
  });
});

describe("MANIFEST_ARCHES", () => {
  it("win e linux publicam manifesto em x64 e arm64", () => {
    expect(MANIFEST_ARCHES.win).toEqual(["x64", "arm64"]);
    expect(MANIFEST_ARCHES.linux).toEqual(["x64", "arm64"]);
  });

  it("mac só publica manifesto em arm64 (Intel descontinuado, par de erro)", () => {
    expect(MANIFEST_ARCHES.mac).toEqual(["arm64"]);
    expect(MANIFEST_ARCHES.mac).not.toContain("x64");
  });
});

describe("computeRetention", () => {
  it("histórico vazio: primeira publicação vira o único item retido", () => {
    const { retained, pruned } = computeRetention([], "0.1.0");
    expect(retained).toEqual(["0.1.0"]);
    expect(pruned).toEqual([]);
  });

  it("abaixo do limite: nada é podado", () => {
    const { retained, pruned } = computeRetention(["0.1.0", "0.1.1"], "0.1.2");
    expect(retained).toEqual(["0.1.0", "0.1.1", "0.1.2"]);
    expect(pruned).toEqual([]);
  });

  it("exatamente no limite (RETENTION_COUNT=3): nada é podado", () => {
    const { retained, pruned } = computeRetention(
      ["0.1.0", "0.1.1"],
      "0.1.2",
      3,
    );
    expect(retained).toHaveLength(3);
    expect(pruned).toEqual([]);
  });

  it("um acima do limite: poda só a mais antiga (par de erro)", () => {
    const { retained, pruned } = computeRetention(
      ["0.1.0", "0.1.1", "0.1.2"],
      "0.1.3",
      3,
    );
    expect(retained).toEqual(["0.1.1", "0.1.2", "0.1.3"]);
    expect(pruned).toEqual(["0.1.0"]);
  });

  it("republicar a mesma versão não duplica nem poda a si mesma", () => {
    const { retained, pruned } = computeRetention(
      ["0.1.0", "0.1.1", "0.1.2"],
      "0.1.2",
      3,
    );
    expect(retained).toEqual(["0.1.0", "0.1.1", "0.1.2"]);
    expect(pruned).toEqual([]);
  });

  it("retain=0 ou negativo (edge): nunca poda a versão recém-publicada", () => {
    const { retained, pruned } = computeRetention(["0.1.0"], "0.1.1", 0);
    expect(retained).toEqual(["0.1.1"]);
    expect(pruned).toEqual(["0.1.0"]);
  });

  it("RETENTION_COUNT default é 3", () => {
    expect(RETENTION_COUNT).toBe(3);
  });
});

describe("r2ClientConfig", () => {
  const FULL_ENV = {
    CLOUDFLARE_ACCOUNT_ID: "acc-123",
    R2_ACCESS_KEY_ID: "key-id",
    R2_SECRET_ACCESS_KEY: "key-secret",
  };

  it("monta endpoint S3 do R2 com credenciais e region auto", () => {
    const config = r2ClientConfig(FULL_ENV);
    expect(config.endpoint).toBe("https://acc-123.r2.cloudflarestorage.com");
    expect(config.region).toBe("auto");
    expect(config.credentials).toEqual({
      accessKeyId: "key-id",
      secretAccessKey: "key-secret",
    });
  });

  it("credencial ausente → erro nomeando exatamente as que faltam (par de erro)", () => {
    expect(() => r2ClientConfig({ CLOUDFLARE_ACCOUNT_ID: "acc-123" })).toThrow(
      /R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY/,
    );
    expect(() => r2ClientConfig({})).toThrow(
      /CLOUDFLARE_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY/,
    );
  });

  it("credencial vazia conta como ausente (edge: env var declarada sem valor)", () => {
    expect(() =>
      r2ClientConfig({ ...FULL_ENV, R2_SECRET_ACCESS_KEY: "" }),
    ).toThrow(/R2_SECRET_ACCESS_KEY/);
  });
});

describe("buildArchManifest / resolveInstaller — regressão do manifesto cross-arch", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "vectora-release-test-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("manifesto de x64 nunca aponta pro instalador de arm64 (bug real corrigido)", () => {
    const x64Path = join(dir, "Vectora-0.1.1-win-x64.exe");
    const arm64Path = join(dir, "Vectora-0.1.1-win-arm64.exe");
    writeFileSync(x64Path, "conteudo-x64");
    writeFileSync(arm64Path, "conteudo-arm64-diferente");

    const installers = new Map([
      ["win/x64", { filename: "Vectora-0.1.1-win-x64.exe", path: x64Path }],
      [
        "win/arm64",
        { filename: "Vectora-0.1.1-win-arm64.exe", path: arm64Path },
      ],
    ]);

    const x64Installer = resolveInstaller(
      installers,
      "win",
      "x64",
      "latest.yml",
    );
    const arm64Installer = resolveInstaller(
      installers,
      "win",
      "arm64",
      "latest.yml",
    );
    const x64Manifest = buildArchManifest(
      "0.1.1",
      x64Installer.path,
      x64Installer.filename,
    );
    const arm64Manifest = buildArchManifest(
      "0.1.1",
      arm64Installer.path,
      arm64Installer.filename,
    );

    expect(x64Manifest.path).toBe("Vectora-0.1.1-win-x64.exe");
    expect(x64Manifest.files[0].url).toBe("Vectora-0.1.1-win-x64.exe");
    expect(arm64Manifest.path).toBe("Vectora-0.1.1-win-arm64.exe");
    expect(arm64Manifest.files[0].url).toBe("Vectora-0.1.1-win-arm64.exe");
    expect(x64Manifest.path).not.toBe(arm64Manifest.path);
    expect(x64Manifest.sha512).not.toBe(arm64Manifest.sha512);
  });

  it("YAML serializado é o que o worker consome (version/files/path/sha512)", () => {
    const filePath = join(dir, "Vectora-0.2.0-win-x64.exe");
    writeFileSync(filePath, "binario-fake");
    const manifest = buildArchManifest(
      "0.2.0",
      filePath,
      "Vectora-0.2.0-win-x64.exe",
    );

    const parsed = parseYaml(stringifyYaml(manifest));
    expect(parsed.version).toBe("0.2.0");
    expect(parsed.path).toBe("Vectora-0.2.0-win-x64.exe");
    expect(parsed.files).toEqual([
      {
        url: "Vectora-0.2.0-win-x64.exe",
        sha512: manifest.sha512,
        size: manifest.files[0].size,
      },
    ]);
  });

  it("sha512Base64 muda quando o conteúdo do arquivo muda (par de erro)", () => {
    const a = join(dir, "a.exe");
    const b = join(dir, "b.exe");
    writeFileSync(a, "conteudo-1");
    writeFileSync(b, "conteudo-2");
    expect(sha512Base64(a)).not.toBe(sha512Base64(b));
  });

  it("resolveInstaller lança quando a arch declarada não tem instalador (par de erro)", () => {
    const installers = new Map<string, { filename: string; path: string }>();
    expect(() =>
      resolveInstaller(installers, "win", "arm64", "latest.yml"),
    ).toThrow(/win\/arm64/);
  });

  it("indexInstallersByOsArch ignora instalador de versão anterior sobrando em dist (achado CodeRabbit)", () => {
    writeFileSync(join(dir, "Vectora-0.1.0-win-x64.exe"), "versao-antiga");
    writeFileSync(join(dir, "Vectora-0.1.1-win-x64.exe"), "versao-atual");

    const installers = indexInstallersByOsArch(
      ["Vectora-0.1.0-win-x64.exe", "Vectora-0.1.1-win-x64.exe"],
      dir,
      "0.1.1",
    );

    expect(installers.get("win/x64")?.filename).toBe(
      "Vectora-0.1.1-win-x64.exe",
    );
  });

  it("indexInstallersByOsArch não indexa nada quando só existe instalador de outra versão (par de erro)", () => {
    writeFileSync(join(dir, "Vectora-0.1.0-win-x64.exe"), "versao-antiga");

    const installers = indexInstallersByOsArch(
      ["Vectora-0.1.0-win-x64.exe"],
      dir,
      "0.1.1",
    );

    expect(installers.size).toBe(0);
  });

  it("indexInstallersByOsArch preserva todos os formatos por SO/arquitetura", () => {
    writeFileSync(join(dir, "Vectora-0.1.1-linux-x64.AppImage"), "appimage");
    writeFileSync(join(dir, "Vectora-0.1.1-linux-x64.deb"), "deb");
    writeFileSync(join(dir, "Vectora-0.1.1-linux-x64.rpm"), "rpm");

    const installers = indexInstallersByOsArch(
      [
        "Vectora-0.1.1-linux-x64.deb",
        "Vectora-0.1.1-linux-x64.AppImage",
        "Vectora-0.1.1-linux-x64.rpm",
      ],
      dir,
      "0.1.1",
    );
    expect(installers.get("linux/x64")?.filename).toBe(
      "Vectora-0.1.1-linux-x64.AppImage",
    );
    expect(installers.get("linux/x64")?.availableFiles).toEqual([
      "Vectora-0.1.1-linux-x64.AppImage",
      "Vectora-0.1.1-linux-x64.deb",
      "Vectora-0.1.1-linux-x64.rpm",
    ]);
  });

  it("seleciona apenas o artefato do auto-updater sem limitar os downloads", () => {
    expect(
      selectManifestInstaller("win", [
        "Vectora-0.1.1-win-x64.msi",
        "Vectora-0.1.1-win-x64.exe",
      ]),
    ).toBe("Vectora-0.1.1-win-x64.exe");
    expect(() =>
      selectManifestInstaller("linux", [
        "Vectora-0.1.1-linux-x64.deb",
        "Vectora-0.1.1-linux-x64.rpm",
      ]),
    ).toThrow(/\.AppImage/);
  });

  it("escolhe o mesmo artefato do manifesto independentemente da ordem", () => {
    const candidates = [
      "Vectora-0.1.1-win-x64.exe",
      "Vectora-0.1.1-win-x64.msi",
    ];
    expect(selectManifestInstaller("win", candidates)).toBe(candidates[0]);
    expect(selectManifestInstaller("win", [...candidates].reverse())).toBe(
      candidates[0],
    );
  });

  it("recusa indexar um manifesto Linux sem AppImage", () => {
    writeFileSync(join(dir, "Vectora-0.1.1-linux-x64.deb"), "deb");
    expect(() =>
      indexInstallersByOsArch(["Vectora-0.1.1-linux-x64.deb"], dir, "0.1.1"),
    ).toThrow(/\.AppImage/);
  });
});

import { describe, it, expect, vi } from "vitest";
import app, {
  rolloutBucket,
  resolveVersion,
  installerFilename,
  parseDownloadTarget,
  processUpdateTelemetry,
} from "../../src/updates/worker";

describe("rolloutBucket", () => {
  it("é determinístico — mesmo token, mesmo bucket", () => {
    expect(rolloutBucket("client-abc")).toBe(rolloutBucket("client-abc"));
  });

  it("sempre no intervalo [0,99], inclusive vazio/unicode", () => {
    for (const t of ["a", "bbbb", "token-123", "", "🚀-cliente"]) {
      const b = rolloutBucket(t);
      expect(b).toBeGreaterThanOrEqual(0);
      expect(b).toBeLessThan(100);
    }
  });

  it("distribui — tokens diferentes não caem todos no mesmo bucket", () => {
    const buckets = new Set(
      ["a", "b", "c", "d", "e", "f", "g", "h"].map(rolloutBucket),
    );
    expect(buckets.size).toBeGreaterThan(1);
  });
});

describe("resolveVersion", () => {
  const base = {
    channels: {
      latest: {
        version: "1.2.0",
        rollout_percent: 100,
        previous_stable: "1.1.0",
      },
    },
    quarantined: [] as string[],
  };

  it("canal desconhecido → null", () => {
    expect(resolveVersion(base, "beta", "tok")).toBeNull();
  });

  it("rollout 100% → serve a versão nova", () => {
    expect(resolveVersion(base, "latest", "tok")).toBe("1.2.0");
  });

  it("rollout 0% → serve o previous_stable", () => {
    const cfg = {
      ...base,
      channels: {
        latest: { ...base.channels.latest, rollout_percent: 0 },
      },
    };
    expect(resolveVersion(cfg, "latest", "tok")).toBe("1.1.0");
  });

  it("versão quarentinada → rollback para previous_stable", () => {
    const cfg = { ...base, quarantined: ["1.2.0"] };
    expect(resolveVersion(cfg, "latest", "tok")).toBe("1.1.0");
  });

  it("quarentinada sem previous_stable → null (edge)", () => {
    const cfg = {
      channels: { latest: { version: "1.2.0", rollout_percent: 100 } },
      quarantined: ["1.2.0"],
    };
    expect(resolveVersion(cfg, "latest", "tok")).toBeNull();
  });
});

describe("installerFilename", () => {
  it("segue o artifactName do electron-builder (${productName}-${version}-${os}-${arch}.${ext})", () => {
    expect(installerFilename("1.2.0", "win", "x64", "exe")).toBe(
      "Vectora-1.2.0-win-x64.exe",
    );
    expect(installerFilename("1.2.0", "mac", "arm64", "dmg")).toBe(
      "Vectora-1.2.0-mac-arm64.dmg",
    );
  });
});

describe("parseDownloadTarget", () => {
  it("separa os-arch.ext em campos", () => {
    expect(parseDownloadTarget("win-x64.exe")).toEqual({
      os: "win",
      arch: "x64",
      ext: "exe",
    });
    expect(parseDownloadTarget("mac-arm64.dmg")).toEqual({
      os: "mac",
      arch: "arm64",
      ext: "dmg",
    });
    expect(parseDownloadTarget("linux-arm64.pkg.tar.zst")).toEqual({
      os: "linux",
      arch: "arm64",
      ext: "pkg.tar.zst",
    });
    expect(parseDownloadTarget("linux-x64.tar.gz")).toEqual({
      os: "linux",
      arch: "x64",
      ext: "tar.gz",
    });
  });

  it("formato inválido (sem hífen, sem extensão, segmentos extras) → null", () => {
    for (const t of [
      "winx64.exe",
      "win-x64",
      "win-x64-extra.exe",
      "linux-x64.pkg.tar.zst.exe",
      "",
    ]) {
      expect(parseDownloadTarget(t)).toBeNull();
    }
  });
});

describe("GET /download/:channel/:target", () => {
  function fakeEnv(opts: { config?: object; fileBody?: string }) {
    const configJson = opts.config ? JSON.stringify(opts.config) : null;
    const KV = {
      get: async () => configJson,
      put: async () => {},
    };
    const R2 = {
      get: async (key: string) => {
        if (!opts.fileBody) return null;
        return {
          body: opts.fileBody,
          httpMetadata: { contentType: "application/octet-stream" },
          httpEtag: `"etag-${key}"`,
        };
      },
    };
    return { KV, R2 };
  }

  it("sem token nenhum — 200 direto (Free não tem conta)", async () => {
    const env = fakeEnv({
      config: {
        channels: { latest: { version: "1.2.0", rollout_percent: 100 } },
        quarantined: [],
      },
      fileBody: "binario-fake",
    });

    const res = await app.request(
      "/download/latest/win-x64.exe",
      {},
      env as never,
    );

    expect(res.status).toBe(200);
    expect(res.headers.get("X-Vectora-Version")).toBe("1.2.0");
    expect(res.headers.get("Content-Disposition")).toContain(
      "Vectora-1.2.0-win-x64.exe",
    );
  });

  it("preserva extensão composta no download do pacote Arch Linux", async () => {
    const env = fakeEnv({
      config: {
        channels: { latest: { version: "1.2.0", rollout_percent: 100 } },
        quarantined: [],
      },
      fileBody: "pacote-pacman",
    });

    const res = await app.request(
      "/download/latest/linux-arm64.pkg.tar.zst",
      {},
      env as never,
    );

    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Disposition")).toContain(
      "Vectora-1.2.0-linux-arm64.pkg.tar.zst",
    );
  });

  it("target em formato inválido → 400", async () => {
    const env = fakeEnv({
      config: {
        channels: { latest: { version: "1.2.0", rollout_percent: 100 } },
        quarantined: [],
      },
      fileBody: "binario-fake",
    });

    const res = await app.request("/download/latest/win-x64", {}, env as never);
    expect(res.status).toBe(400);
  });

  it("canal desconhecido → 404", async () => {
    const env = fakeEnv({
      config: { channels: {}, quarantined: [] },
    });

    const res = await app.request(
      "/download/beta/win-x64.exe",
      {},
      env as never,
    );
    expect(res.status).toBe(404);
  });

  it("versão quarentinada sem previous_stable → 404 (edge)", async () => {
    const env = fakeEnv({
      config: {
        channels: { latest: { version: "1.2.0", rollout_percent: 100 } },
        quarantined: ["1.2.0"],
      },
      fileBody: "binario-fake",
    });

    const res = await app.request(
      "/download/latest/win-x64.exe",
      {},
      env as never,
    );
    expect(res.status).toBe(404);
  });

  it("arquivo não existe no R2 → 404", async () => {
    const env = fakeEnv({
      config: {
        channels: { latest: { version: "1.2.0", rollout_percent: 100 } },
        quarantined: [],
      },
      // sem fileBody — R2.get retorna null
    });

    const res = await app.request(
      "/download/latest/win-x64.exe",
      {},
      env as never,
    );
    expect(res.status).toBe(404);
  });

  it("serve o binário certo quando ele existe no R2", async () => {
    const env = fakeEnv({
      config: {
        channels: { latest: { version: "1.2.0", rollout_percent: 100 } },
        quarantined: [],
      },
      fileBody: "binario-x64",
    });

    const res = await app.request(
      "/download/latest/win-x64.exe",
      {},
      env as never,
    );

    expect(res.status).toBe(200);
    expect(await res.text()).toBe("binario-x64");
  });
});

describe("GET /version/:channel", () => {
  function fakeEnv(opts: { config?: object }) {
    const configJson = opts.config ? JSON.stringify(opts.config) : null;
    const KV = {
      get: async () => configJson,
      put: async () => {},
    };
    return { KV };
  }

  it("canal conhecido — devolve a versão estável do canal", async () => {
    const env = fakeEnv({
      config: {
        channels: { latest: { version: "1.2.0", rollout_percent: 100 } },
        quarantined: [],
      },
    });

    const res = await app.request("/version/latest", {}, env as never);

    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Type")).toContain("application/json");
    await expect(res.json()).resolves.toEqual({
      version: "1.2.0",
      channel: "latest",
    });
  });

  it("versão quarentinada cai pra previous_stable", async () => {
    const env = fakeEnv({
      config: {
        channels: {
          latest: {
            version: "1.3.0",
            rollout_percent: 100,
            previous_stable: "1.2.0",
          },
        },
        quarantined: ["1.3.0"],
      },
    });

    const res = await app.request("/version/latest", {}, env as never);

    await expect(res.json()).resolves.toEqual({
      version: "1.2.0",
      channel: "latest",
    });
  });

  it("canal desconhecido → 404 (erro claro, não crash)", async () => {
    const env = fakeEnv({ config: { channels: {}, quarantined: [] } });

    const res = await app.request("/version/beta", {}, env as never);

    expect(res.status).toBe(404);
  });

  it("versão quarentinada sem previous_stable → 404 (edge)", async () => {
    const env = fakeEnv({
      config: {
        channels: { latest: { version: "1.2.0", rollout_percent: 100 } },
        quarantined: ["1.2.0"],
      },
    });

    const res = await app.request("/version/latest", {}, env as never);

    expect(res.status).toBe(404);
  });
});

describe("GET /updates/:channel/:os/:arch/latest.yml — sem token", () => {
  it("funciona sem token/query nenhum (Free não tem conta)", async () => {
    const KV = {
      get: async () =>
        JSON.stringify({
          channels: { latest: { version: "1.2.0", rollout_percent: 100 } },
          quarantined: [],
        }),
      put: async () => {},
    };
    const R2 = {
      get: async () => ({
        body: "manifest-fake",
        httpMetadata: { contentType: "application/x-yaml" },
        httpEtag: '"etag"',
      }),
    };
    const env = { KV, R2 };

    const res = await app.request(
      "/updates/latest/win/x64/latest.yml",
      {},
      env as never,
    );
    expect(res.status).toBe(200);
  });

  it("sem config nenhuma no KV — trata como canais vazios → 404", async () => {
    const env = {
      KV: { get: async () => null, put: async () => {} },
      R2: { get: async () => null },
    };
    const res = await app.request(
      "/updates/latest/win/x64/latest.yml",
      {},
      env as never,
    );
    expect(res.status).toBe(404);
    expect(await res.text()).toBe("no version available");
  });

  it("canal existe mas o manifesto não está no R2 → 404", async () => {
    const env = {
      KV: {
        get: async () =>
          JSON.stringify({
            channels: { latest: { version: "1.2.0", rollout_percent: 100 } },
            quarantined: [],
          }),
        put: async () => {},
      },
      R2: { get: async () => null },
    };
    const res = await app.request(
      "/updates/latest/win/x64/latest.yml",
      {},
      env as never,
    );
    expect(res.status).toBe(404);
    expect(await res.text()).toBe("manifest missing");
  });

  it("erro de borda — manifesto só existe em outra arch (arm64) → 404, NUNCA serve o manifesto errado", async () => {
    // Bug real reproduzido em produção (0.1.11→0.1.13, Windows): quando o
    // manifesto x64 não existe mas o arm64 sim, o código antigo caía pro
    // arm64 e o servia sob o path x64 — o electron-updater então montava
    // a URL de download combinando o path pedido (win/x64) com o filename
    // do manifesto servido (Vectora-...-win-arm64.exe), uma chave que
    // nunca existe no R2 → "update encontrado" mas download 404 sempre.
    // getExactArchObject nunca deve misturar arch pedida com arch servida.
    const env = {
      KV: {
        get: async () =>
          JSON.stringify({
            channels: { latest: { version: "1.2.0", rollout_percent: 100 } },
            quarantined: [],
          }),
        put: async () => {},
      },
      R2: {
        get: async (key: string) =>
          key.includes("/arm64/")
            ? {
                body: "manifest-arm64",
                httpMetadata: { contentType: "application/x-yaml" },
                httpEtag: '"etag-arm64"',
              }
            : null,
      },
    };

    const res = await app.request(
      "/updates/latest/win/x64/latest.yml",
      {},
      env as never,
    );

    expect(res.status).toBe(404);
    expect(await res.text()).toBe("manifest missing");
  });

  it("erro de borda — manifesto da própria arch existe → 200 com o conteúdo certo (par de acerto do teste acima)", async () => {
    const env = {
      KV: {
        get: async () =>
          JSON.stringify({
            channels: { latest: { version: "1.2.0", rollout_percent: 100 } },
            quarantined: [],
          }),
        put: async () => {},
      },
      R2: {
        get: async (key: string) =>
          key.includes("/x64/")
            ? {
                body: "manifest-x64",
                httpMetadata: { contentType: "application/x-yaml" },
                httpEtag: '"etag-x64"',
              }
            : null,
      },
    };

    const res = await app.request(
      "/updates/latest/win/x64/latest.yml",
      {},
      env as never,
    );

    expect(res.status).toBe(200);
    expect(await res.text()).toBe("manifest-x64");
  });
});

describe("regressão: fluxo completo de update entre versões (0.1.11 → 0.1.13, arch publicada parcialmente)", () => {
  /** Simula R2 com só a build arm64 da nova versão publicada — cenário
   * exato do bug relatado em produção: a build win-x64 da 0.1.13 não
   * tinha sido publicada ainda (ou falhou), só a win-arm64. */
  function fakeEnvArm64Only() {
    const files = new Map<string, string>([
      [
        "latest/win/arm64/1.2.0/latest.yml",
        "version: 1.2.0\npath: Vectora-1.2.0-win-arm64.exe",
      ],
      ["latest/win/arm64/1.2.0/Vectora-1.2.0-win-arm64.exe", "binario-arm64"],
      // win/x64/1.2.0/* deliberadamente ausente do R2.
    ]);
    return {
      KV: {
        get: async () =>
          JSON.stringify({
            channels: { latest: { version: "1.2.0", rollout_percent: 100 } },
            quarantined: [],
          }),
        put: async () => {},
      },
      R2: {
        get: async (key: string) => {
          const body = files.get(key);
          if (!body) return null;
          return {
            body,
            httpMetadata: { contentType: "application/octet-stream" },
            httpEtag: `"etag-${key}"`,
          };
        },
      },
    };
  }

  it("cliente win-x64 checando update: manifesto 404 de forma honesta, nunca um manifesto de outra arch", async () => {
    const env = fakeEnvArm64Only();

    const manifestRes = await app.request(
      "/updates/latest/win/x64/latest.yml",
      {},
      env as never,
    );
    expect(manifestRes.status).toBe(404);

    // Mesmo se o electron-updater ignorasse o 404 e tentasse baixar o
    // instalador arm64 sob o path x64 (o que o bug antigo levava a
    // acontecer), essa combinação de path nunca existe no R2 — 404 limpo,
    // não um 200 com o binário errado.
    const wrongComboRes = await app.request(
      "/updates/latest/win/x64/1.2.0/Vectora-1.2.0-win-arm64.exe",
      {},
      env as never,
    );
    expect(wrongComboRes.status).toBe(404);
  });

  it("cliente win-arm64 checando a mesma release: manifesto e download funcionam normalmente", async () => {
    const env = fakeEnvArm64Only();

    const manifestRes = await app.request(
      "/updates/latest/win/arm64/latest.yml",
      {},
      env as never,
    );
    expect(manifestRes.status).toBe(200);
    expect(await manifestRes.text()).toContain("Vectora-1.2.0-win-arm64.exe");

    const downloadRes = await app.request(
      "/updates/latest/win/arm64/1.2.0/Vectora-1.2.0-win-arm64.exe",
      {},
      env as never,
    );
    expect(downloadRes.status).toBe(200);
    expect(await downloadRes.text()).toBe("binario-arm64");
  });
});

describe("GET /updates/:channel/:os/:arch/:version/:filename", () => {
  it("serve o arquivo do R2 com content-type/etag, 404 se ausente", async () => {
    const found = {
      KV: { get: async () => null, put: async () => {} },
      R2: {
        get: async () => ({
          body: "asset-fake",
          httpMetadata: { contentType: "application/octet-stream" },
          httpEtag: '"etag-1"',
        }),
      },
    };
    const res = await app.request(
      "/updates/latest/win/x64/1.2.0/latest.yml.blockmap",
      {},
      found as never,
    );
    expect(res.status).toBe(200);
    expect(res.headers.get("ETag")).toBe('"etag-1"');

    const missing = {
      KV: { get: async () => null, put: async () => {} },
      R2: { get: async () => null },
    };
    const missingRes = await app.request(
      "/updates/latest/win/x64/1.2.0/latest.yml.blockmap",
      {},
      missing as never,
    );
    expect(missingRes.status).toBe(404);
  });
});

describe("POST /telemetry/update-result", () => {
  it("só enfileira um job update_telemetry, não toca no KV direto", async () => {
    const jobsSend = vi.fn(async () => undefined);
    const env = { JOBS_QUEUE: { send: jobsSend } };

    const res = await app.request(
      "/telemetry/update-result",
      {
        method: "POST",
        body: JSON.stringify({
          state: "failed",
          version: "1.2.0",
          os: "win",
          arch: "x64",
        }),
      },
      env as never,
    );
    expect(res.status).toBe(200);
    expect(jobsSend).toHaveBeenCalledExactlyOnceWith({
      type: "update_telemetry",
      state: "failed",
      version: "1.2.0",
      os: "win",
      arch: "x64",
    });
  });
});

describe("processUpdateTelemetry", () => {
  function fakeKv(initial: Record<string, string> = {}) {
    const store = new Map(Object.entries(initial));
    return {
      get: async (key: string) => store.get(key) ?? null,
      put: async (key: string, value: string) => {
        store.set(key, value);
      },
      _store: store,
    };
  }

  it("increments the failure counter and does not quarantine before the 3rd failure", async () => {
    const KV = fakeKv();
    const env = { KV } as never;

    for (let i = 0; i < 2; i++) {
      await processUpdateTelemetry(env, {
        state: "failed",
        version: "1.2.0",
        os: "win",
        arch: "x64",
      });
    }
    expect(KV._store.get("config")).toBeUndefined();
  });

  it("quarantines the version automatically on the 3rd failure within the window", async () => {
    const KV = fakeKv({ "telem:1.2.0:failed": "2" });
    const env = { KV } as never;

    await processUpdateTelemetry(env, {
      state: "failed",
      version: "1.2.0",
      os: "win",
      arch: "x64",
    });

    const config = JSON.parse(KV._store.get("config")!);
    expect(config.quarantined).toContain("1.2.0");
  });

  it("is idempotent — does not duplicate an already-quarantined version", async () => {
    const KV = fakeKv({
      "telem:1.2.0:failed": "5",
      config: JSON.stringify({ channels: {}, quarantined: ["1.2.0"] }),
    });
    const env = { KV } as never;

    await processUpdateTelemetry(env, {
      state: "failed",
      version: "1.2.0",
      os: "win",
      arch: "x64",
    });

    const config = JSON.parse(KV._store.get("config")!);
    expect(config.quarantined).toEqual(["1.2.0"]);
  });

  it("does not quarantine on a success or started state", async () => {
    const KV = fakeKv({ "telem:1.2.0:failed": "10" });
    const env = { KV } as never;

    await processUpdateTelemetry(env, {
      state: "completed",
      version: "1.2.0",
      os: "win",
      arch: "x64",
    });

    expect(KV._store.get("config")).toBeUndefined();
  });
});

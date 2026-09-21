/**
 * Tests para o `library-store`: cache TTL entre unmount/remount das seções
 * da aba Library (o bug real era refetch a cada fechar/reabrir accordion).
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { skillTrustLevel, useLibraryStore } from "../library-store";

function resetStore() {
  useLibraryStore.setState({
    mcpItems: [],
    mcpInstalledIds: new Set(),
    mcpLoading: false,
    mcpFetchedAt: null,
    mcpQuery: "",
    mcpError: null,
    skillsItems: [],
    skillsLoading: false,
    skillsFetchedAt: null,
    skillsQuery: "",
    skillsError: null,
    memoryItems: [],
    memoryLoading: false,
    memoryFetchedAt: null,
    memoryQuery: "",
    memoryError: null,
    extensionItems: [],
    extensionLoading: false,
    extensionFetchedAt: null,
    extensionQuery: "",
    extensionError: null,
    extensionInstalledIds: new Set(),
    extensionInstallingId: null,
  });
}

beforeEach(() => {
  resetStore();
  vi.restoreAllMocks();
});

describe("library-store — MCP", () => {
  it("ensureMcpLoaded busca registry+instalados na primeira chamada", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (String(url).includes("/mcp/registry")) {
        return {
          ok: true,
          json: async () => [{ id: "a", name: "A" } as never],
        } as Response;
      }
      return {
        ok: true,
        json: async () => ({ servers: [{ name: "a" }] }),
      } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    await useLibraryStore.getState().ensureMcpLoaded();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(useLibraryStore.getState().mcpItems).toHaveLength(1);
    expect(useLibraryStore.getState().mcpInstalledIds.has("a")).toBe(true);
  });

  it("dentro do TTL, chamada repetida não refaz fetch (fix do bug de refetch ao reabrir)", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => [],
    })) as unknown as typeof fetch;
    vi.stubGlobal("fetch", fetchMock);

    await useLibraryStore.getState().ensureMcpLoaded();
    const callsAfterFirst = (fetchMock as ReturnType<typeof vi.fn>).mock.calls
      .length;
    await useLibraryStore.getState().ensureMcpLoaded();

    expect((fetchMock as ReturnType<typeof vi.fn>).mock.calls.length).toBe(
      callsAfterFirst,
    );
  });

  it("erro/borda: fetch expirado (fetchedAt antigo) refaz a busca", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => [],
    })) as unknown as typeof fetch;
    vi.stubGlobal("fetch", fetchMock);

    await useLibraryStore.getState().ensureMcpLoaded();
    useLibraryStore.setState({ mcpFetchedAt: Date.now() - 10 * 60 * 1000 });
    await useLibraryStore.getState().ensureMcpLoaded();

    expect(
      (fetchMock as ReturnType<typeof vi.fn>).mock.calls.length,
    ).toBeGreaterThan(2);
  });

  it("invalidateMcp força refetch na próxima chamada", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => [],
    })) as unknown as typeof fetch;
    vi.stubGlobal("fetch", fetchMock);

    await useLibraryStore.getState().ensureMcpLoaded();
    useLibraryStore.getState().invalidateMcp();
    await useLibraryStore.getState().ensureMcpLoaded();

    expect(
      (fetchMock as ReturnType<typeof vi.fn>).mock.calls.length,
    ).toBeGreaterThan(2);
  });

  it("ensureMcpLoaded('brave') propaga q pro backend; erro na busca seguinte mantém itens antigos e seta mcpError", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (String(url).startsWith("/mcp/registry")) {
        return {
          ok: true,
          json: async () => [{ id: "brave-search", name: "Brave" } as never],
        } as Response;
      }
      return { ok: true, json: async () => ({ servers: [] }) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    await useLibraryStore.getState().ensureMcpLoaded("brave");

    const registryCall = fetchMock.mock.calls.find((c) =>
      String(c[0]).startsWith("/mcp/registry"),
    )!;
    expect(registryCall[0]).toBe("/mcp/registry?q=brave");
    expect(useLibraryStore.getState().mcpItems).toHaveLength(1);
    expect(useLibraryStore.getState().mcpError).toBeNull();

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      }),
    );
    await useLibraryStore.getState().ensureMcpLoaded("outra-busca");

    expect(useLibraryStore.getState().mcpItems).toHaveLength(1);
    expect(useLibraryStore.getState().mcpItems[0].id).toBe("brave-search");
    expect(useLibraryStore.getState().mcpError).not.toBeNull();
  });
});

describe("library-store — Skills e Memory", () => {
  it("ensureSkillsLoaded popula skillsItems e não refetch dentro do TTL", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ entries: [{ id: "s1", name: "Skill" }] }),
    })) as unknown as typeof fetch;
    vi.stubGlobal("fetch", fetchMock);

    await useLibraryStore.getState().ensureSkillsLoaded();
    await useLibraryStore.getState().ensureSkillsLoaded();

    expect(useLibraryStore.getState().skillsItems).toHaveLength(1);
    expect((fetchMock as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1);
  });

  it("erro/borda: resposta não-ok em skills não lança, mantém itens antigos e seta skillsError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ entries: [{ id: "s0", name: "Old" }] }),
      })),
    );
    await useLibraryStore.getState().ensureSkillsLoaded();
    expect(useLibraryStore.getState().skillsItems).toHaveLength(1);

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, json: async () => ({}) })),
    );

    await expect(
      useLibraryStore.getState().ensureSkillsLoaded("nova-busca"),
    ).resolves.not.toThrow();
    expect(useLibraryStore.getState().skillsItems).toHaveLength(1);
    expect(useLibraryStore.getState().skillsItems[0].id).toBe("s0");
    expect(useLibraryStore.getState().skillsError).not.toBeNull();
  });

  it("ensureSkillsLoaded('pdf') propaga q pro backend via querystring", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ entries: [] }),
    })) as unknown as typeof fetch;
    vi.stubGlobal("fetch", fetchMock);

    await useLibraryStore.getState().ensureSkillsLoaded("pdf");

    expect((fetchMock as ReturnType<typeof vi.fn>).mock.calls[0][0]).toBe(
      "/skills/catalog?q=pdf",
    );
  });

  it("ensureMemoryLoaded popula memoryItems e não refetch dentro do TTL", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => [{ id: "m1", name: "Bucket" }],
    })) as unknown as typeof fetch;
    vi.stubGlobal("fetch", fetchMock);

    await useLibraryStore.getState().ensureMemoryLoaded();
    await useLibraryStore.getState().ensureMemoryLoaded();

    expect(useLibraryStore.getState().memoryItems).toHaveLength(1);
    expect((fetchMock as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1);
  });

  it("ensureMemoryLoaded('docs') propaga q; erro na busca seguinte mantém lista antiga e seta memoryError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => ({
        ok: true,
        json: async () =>
          String(url).includes("?q=docs") ? [{ id: "m1", name: "Docs" }] : [],
      })),
    );
    await useLibraryStore.getState().ensureMemoryLoaded("docs");
    expect(useLibraryStore.getState().memoryItems).toHaveLength(1);

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("falhou");
      }),
    );
    await useLibraryStore.getState().ensureMemoryLoaded("outra");

    expect(useLibraryStore.getState().memoryItems).toHaveLength(1);
    expect(useLibraryStore.getState().memoryError).not.toBeNull();
  });
});

describe("library-store — skillTrustLevel", () => {
  it("vectora_verified vence verified — sempre builtin quando os dois são true", () => {
    expect(
      skillTrustLevel({
        id: "s1",
        name: "s",
        description: "",
        source: "",
        vectora_verified: true,
        verified: true,
      }),
    ).toBe("builtin");
  });

  it("só verified (curadoria de admin, sem selo oficial) é 'verified'", () => {
    expect(
      skillTrustLevel({
        id: "s2",
        name: "s",
        description: "",
        source: "",
        verified: true,
      }),
    ).toBe("verified");
  });

  it("erro/borda: sem nenhum dos dois campos (undefined) cai em community, nunca lança", () => {
    expect(
      skillTrustLevel({ id: "s3", name: "s", description: "", source: "" }),
    ).toBe("community");
  });
});

describe("library-store — VEXT", () => {
  it("carrega catálogo e restaura extensões ativas do ciclo local", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/registry/extensions") {
        return {
          ok: true,
          json: async () => ({
            entries: [{ id: "prettier", name: "Prettier" }],
          }),
        } as Response;
      }
      return {
        ok: true,
        json: async () => ({
          extensions: [
            { id: "prettier", version: "1.0.0", active: true },
            { id: "old", version: "1.0.0", active: false },
          ],
        }),
      } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    await useLibraryStore.getState().ensureExtensionsLoaded();

    expect(useLibraryStore.getState().extensionItems).toHaveLength(1);
    expect(useLibraryStore.getState().extensionInstalledIds).toEqual(
      new Set(["prettier"]),
    );
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

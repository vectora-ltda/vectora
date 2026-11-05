/**
 * Tests para o auth-store: setUser/clearUser e hydrate (200, 401+refresh,
 * offline). Tokens nunca tocam o store (ficam em cookies httpOnly).
 */

import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { useAuthStore } from "../auth-store";
import type { AuthUser } from "@/lib/types/auth";

const USER = { id: "u1", email: "a@b.com" } as unknown as AuthUser;

function jsonRes(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

beforeEach(() => {
  useAuthStore.setState({ user: null, isAuthenticated: false });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("auth-store — síncrono", () => {
  it("setUser define user e isAuthenticated", () => {
    useAuthStore.getState().setUser(USER);
    expect(useAuthStore.getState().user).toEqual(USER);
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });

  it("clearUser zera a sessão local", () => {
    useAuthStore.getState().setUser(USER);
    useAuthStore.getState().clearUser();
    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  it("isAuthenticated começa false", () => {
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    expect(useAuthStore.getState().user).toBeNull();
  });

  it("setUser substitui o usuário anterior", () => {
    useAuthStore.getState().setUser(USER);
    const other = { id: "u2", email: "c@d.com" } as unknown as AuthUser;
    useAuthStore.getState().setUser(other);
    expect(useAuthStore.getState().user).toEqual(other);
  });

  it("clearUser é idempotente quando já está nulo", () => {
    useAuthStore.getState().clearUser();
    useAuthStore.getState().clearUser();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});

describe("auth-store — hydrate", () => {
  it("200 em /auth/me autentica", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonRes(USER)),
    );
    await useAuthStore.getState().hydrate();
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    expect(useAuthStore.getState().user).toEqual(USER);
  });

  it("401 → refresh ok → /auth/me ok autentica", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonRes(null, false, 401)) // /auth/me
      .mockResolvedValueOnce(jsonRes({}, true)) // /auth/refresh
      .mockResolvedValueOnce(jsonRes(USER, true)); // /auth/me de novo
    vi.stubGlobal("fetch", fetchMock);
    await useAuthStore.getState().hydrate();
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });

  it("401 → refresh falha → limpa a sessão", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonRes(null, false, 401))
      .mockResolvedValueOnce(jsonRes(null, false, 401));
    vi.stubGlobal("fetch", fetchMock);
    useAuthStore.getState().setUser(USER);
    await useAuthStore.getState().hydrate();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  it("backend offline preserva o estado anterior", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      }),
    );
    useAuthStore.getState().setUser(USER);
    await useAuthStore.getState().hydrate();
    // Não muda nada — cache anterior continua válido.
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });

  it("chama /auth/me com credentials include", async () => {
    const fetchMock = vi.fn((..._args: unknown[]) =>
      Promise.resolve(jsonRes(USER)),
    );
    vi.stubGlobal("fetch", fetchMock);
    await useAuthStore.getState().hydrate();
    const [url, opts] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/auth/me");
    expect((opts as RequestInit).credentials).toBe("include");
  });

  it("200 seta o objeto user exato do backend", async () => {
    const fresh = { id: "u9", email: "z@z.com" } as unknown as AuthUser;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonRes(fresh)),
    );
    await useAuthStore.getState().hydrate();
    expect(useAuthStore.getState().user).toEqual(fresh);
  });

  it("403 (não-401) limpa a sessão", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonRes(null, false, 403)),
    );
    useAuthStore.getState().setUser(USER);
    await useAuthStore.getState().hydrate();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  it("500 limpa a sessão", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonRes(null, false, 500)),
    );
    useAuthStore.getState().setUser(USER);
    await useAuthStore.getState().hydrate();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  it("401 → refresh ok → segundo /auth/me falha → limpa", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonRes(null, false, 401)) // /auth/me
      .mockResolvedValueOnce(jsonRes({}, true)) // /auth/refresh ok
      .mockResolvedValueOnce(jsonRes(null, false, 401)); // /auth/me de novo falha
    vi.stubGlobal("fetch", fetchMock);
    useAuthStore.getState().setUser(USER);
    await useAuthStore.getState().hydrate();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  it("401 → refresh lança → limpa (catch interno)", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonRes(null, false, 401))
      .mockRejectedValueOnce(new Error("refresh blew up"));
    vi.stubGlobal("fetch", fetchMock);
    useAuthStore.getState().setUser(USER);
    await useAuthStore.getState().hydrate();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  it("401 → refresh é POST com credentials include", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonRes(null, false, 401))
      .mockResolvedValueOnce(jsonRes({}, true))
      .mockResolvedValueOnce(jsonRes(USER, true));
    vi.stubGlobal("fetch", fetchMock);
    await useAuthStore.getState().hydrate();
    const refreshCall = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes("/auth/refresh"),
    );
    expect((refreshCall![1] as RequestInit).method).toBe("POST");
    expect((refreshCall![1] as RequestInit).credentials).toBe("include");
  });

  it("401 → refresh ok seta o user do segundo /auth/me", async () => {
    const refreshed = { id: "ur", email: "r@r.com" } as unknown as AuthUser;
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonRes(null, false, 401))
      .mockResolvedValueOnce(jsonRes({}, true))
      .mockResolvedValueOnce(jsonRes(refreshed, true));
    vi.stubGlobal("fetch", fetchMock);
    await useAuthStore.getState().hydrate();
    expect(useAuthStore.getState().user).toEqual(refreshed);
  });

  it("offline com sessão nula permanece não autenticado", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("down");
      }),
    );
    await useAuthStore.getState().hydrate();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    expect(useAuthStore.getState().user).toBeNull();
  });
});

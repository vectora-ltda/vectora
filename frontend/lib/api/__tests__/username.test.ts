import { describe, it, expect, vi, afterEach } from "vitest";
import { slugifyUsername, checkUsername } from "../username";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("slugifyUsername", () => {
  it("deriva slug do nome composto", () => {
    expect(slugifyUsername("Bruno Soares")).toBe("brunosoares");
  });

  it("remove acento preservando a letra base", () => {
    expect(slugifyUsername("José")).toBe("jose");
  });

  it("devolve vazio quando não sobra caractere aproveitável", () => {
    expect(slugifyUsername("!!! ??? ")).toBe("");
  });
});

describe("checkUsername", () => {
  it("devolve o status parseado quando ok", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          normalized: "bruno",
          available: false,
          suggestion: "bruno#4821",
        }),
      }),
    );
    const status = await checkUsername("bruno");
    expect(status).toEqual({
      normalized: "bruno",
      available: false,
      suggestion: "bruno#4821",
    });
  });

  it("codifica o username na query", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ normalized: "a", available: true, suggestion: "a" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    await checkUsername("a b#c");
    expect(fetchMock).toHaveBeenCalledWith(
      "/auth/username-available?username=a%20b%23c",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("devolve null quando a resposta não é ok", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    expect(await checkUsername("x")).toBeNull();
  });

  it("devolve null em falha de rede (não lança)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    await expect(checkUsername("x")).resolves.toBeNull();
  });
});

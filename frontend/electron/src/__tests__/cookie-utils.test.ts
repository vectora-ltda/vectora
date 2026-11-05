import { describe, it, expect } from "vitest";
import { parseSetCookieHeader, buildCookieHeader } from "../cookie-utils.js";

describe("parseSetCookieHeader", () => {
  it("parseia access token padrão do backend", () => {
    const r = parseSetCookieHeader(
      "vectora_access=TOKEN123; HttpOnly; Path=/; SameSite=lax; Max-Age=900",
    );
    expect(r).not.toBeNull();
    expect(r!.name).toBe("vectora_access");
    expect(r!.value).toBe("TOKEN123");
    expect(r!.httpOnly).toBe(true);
    expect(r!.attrs["path"]).toBe("/");
    expect(r!.attrs["samesite"]).toBe("lax");
    expect(r!.attrs["max-age"]).toBe("900");
  });

  it("parseia refresh token padrão do backend", () => {
    const r = parseSetCookieHeader(
      "vectora_refresh=REFRESH456; HttpOnly; Path=/; SameSite=lax; Max-Age=604800",
    );
    expect(r!.name).toBe("vectora_refresh");
    expect(r!.value).toBe("REFRESH456");
    expect(r!.attrs["max-age"]).toBe("604800");
  });

  it("httpOnly é false quando atributo está ausente", () => {
    const r = parseSetCookieHeader("session=abc; Path=/");
    expect(r!.httpOnly).toBe(false);
  });

  it("retorna null para string sem '='", () => {
    expect(parseSetCookieHeader("invalido")).toBeNull();
    expect(parseSetCookieHeader("")).toBeNull();
  });

  it("retorna null para nome vazio", () => {
    expect(parseSetCookieHeader("=valor")).toBeNull();
  });

  it("cookie com valor contendo '=' mantém o valor inteiro", () => {
    const r = parseSetCookieHeader("jwt=a.b.c==; Path=/");
    expect(r!.value).toBe("a.b.c==");
  });

  it("Max-Age=0 é parseado como string '0' (deleção)", () => {
    const r = parseSetCookieHeader(
      "vectora_access=; Path=/; Max-Age=0; HttpOnly",
    );
    expect(r!.attrs["max-age"]).toBe("0");
    expect(parseInt(r!.attrs["max-age"], 10)).toBeLessThanOrEqual(0);
  });

  it("atributo Secure (sem '=') não aparece em attrs mas não impede parse", () => {
    const r = parseSetCookieHeader("tok=VAL; Secure; HttpOnly; Path=/");
    expect(r!.httpOnly).toBe(true);
    expect(r!.attrs["secure"]).toBeUndefined();
    expect(r!.value).toBe("VAL");
  });

  it("chaves de atributos são normalizadas para lowercase", () => {
    const r = parseSetCookieHeader("x=y; PATH=/admin; SAMESITE=Strict");
    expect(r!.attrs["path"]).toBe("/admin");
    expect(r!.attrs["samesite"]).toBe("Strict");
  });

  it("SameSite=none é parseado corretamente", () => {
    const r = parseSetCookieHeader("tok=X; SameSite=none; Secure");
    expect(r!.attrs["samesite"]).toBe("none");
  });

  it("espaços extras em torno do nome/valor são removidos", () => {
    const r = parseSetCookieHeader("  myname  =  myval  ; Path=/");
    expect(r!.name).toBe("myname");
    expect(r!.value).toBe("myval");
  });
});

describe("buildCookieHeader", () => {
  it("store vazio retorna string vazia", () => {
    expect(buildCookieHeader(new Map())).toBe("");
  });

  it("um cookie", () => {
    const store = new Map([["tok", "ABC"]]);
    expect(buildCookieHeader(store)).toBe("tok=ABC");
  });

  it("dois cookies separados por '; '", () => {
    const store = new Map<string, string>([
      ["vectora_access", "TOKEN1"],
      ["vectora_refresh", "TOKEN2"],
    ]);
    expect(buildCookieHeader(store)).toBe(
      "vectora_access=TOKEN1; vectora_refresh=TOKEN2",
    );
  });

  it("valores com caracteres especiais não são alterados", () => {
    const store = new Map([["jwt", "a.b.c=="]]);
    expect(buildCookieHeader(store)).toBe("jwt=a.b.c==");
  });

  it("preserva a ordem de inserção do Map", () => {
    const store = new Map<string, string>([
      ["a", "1"],
      ["b", "2"],
      ["c", "3"],
    ]);
    expect(buildCookieHeader(store)).toBe("a=1; b=2; c=3");
  });
});

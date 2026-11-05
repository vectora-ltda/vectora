// @vitest-environment jsdom
/**
 * Tests para lib/queries/license: useLicenseQuery. O fetch trata 503 como
 * resposta válida (servidor sem licença ainda), mas lança em outros erros.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { createElement, type ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { useLicenseQuery, licenseQueryKey } from "@/lib/queries/license";

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children);
  return { qc, wrapper };
}

function res(
  body: unknown,
  { ok = true, status = 200 }: { ok?: boolean; status?: number } = {},
): Response {
  return { ok, status, json: async () => body } as unknown as Response;
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("licenseQueryKey", () => {
  it("é ['license','status']", () => {
    expect(licenseQueryKey).toEqual(["license", "status"]);
  });
});

describe("useLicenseQuery", () => {
  it("carrega o status quando ok", async () => {
    fetchMock.mockResolvedValueOnce(res({ tier: "pro", state: "active" }));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useLicenseQuery(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toMatchObject({ tier: "pro" });
  });

  it("503 é tratado como resposta válida (não erro)", async () => {
    fetchMock.mockResolvedValueOnce(
      res({ tier: "free", state: "unlicensed" }, { ok: false, status: 503 }),
    );
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useLicenseQuery(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toMatchObject({ state: "unlicensed" });
  });

  it("erro não-503 vira isError", async () => {
    fetchMock.mockResolvedValueOnce(res(null, { ok: false, status: 500 }));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useLicenseQuery(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it("chama /license/status com Accept application/json", async () => {
    fetchMock.mockResolvedValueOnce(res({ tier: "free" }));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useLicenseQuery(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const [url, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain("/license/status");
    expect((opts.headers as Record<string, string>).Accept).toBe(
      "application/json",
    );
  });

  it("não faz retry em erro (retry: false)", async () => {
    fetchMock.mockResolvedValue(res(null, { ok: false, status: 500 }));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useLicenseQuery(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

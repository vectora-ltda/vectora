// @vitest-environment jsdom
/**
 * Tests para useUserId: UUID anônimo do browser persistido em localStorage.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { waitFor, renderHook } from "@testing-library/react";
import { useUserId } from "../use-user-id";

const KEY = "vectora-user-id";

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(console, "info").mockImplementation(() => {});
});

describe("useUserId", () => {
  it("gera e persiste um id novo quando não existe", async () => {
    const { result } = renderHook(() => useUserId());
    await waitFor(() => expect(result.current).not.toBeNull());
    expect(result.current).toMatch(/^user-/);
    expect(localStorage.getItem(KEY)).toBe(result.current);
  });

  it("reaproveita o id existente do localStorage", async () => {
    localStorage.setItem(KEY, "user-fixo-123");
    const { result } = renderHook(() => useUserId());
    await waitFor(() => expect(result.current).toBe("user-fixo-123"));
  });
});

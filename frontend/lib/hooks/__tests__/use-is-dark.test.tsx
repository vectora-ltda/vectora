// @vitest-environment jsdom
import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useIsDark } from "../use-is-dark";

let theme: "light" | "dark" | "system" = "light";

vi.mock("@/lib/stores/settings-store", () => ({
  useSettingsStore: (selector: (state: { theme: typeof theme }) => unknown) =>
    selector({ theme }),
}));

describe("useIsDark", () => {
  beforeEach(() => {
    theme = "light";
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({
        matches: true,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );
  });

  it("sincroniza a preferência do sistema ao entrar no modo system", () => {
    const { result, rerender } = renderHook(() => useIsDark());
    expect(result.current).toBe(false);

    theme = "system";
    rerender();

    expect(result.current).toBe(true);
  });
});

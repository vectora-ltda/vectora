// @vitest-environment jsdom
/**
 * usePins — carrega os pins da sessão do backend ao montar/trocar de thread.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";

const loadPins = vi.fn();

vi.mock("@/lib/stores/workbench-store", () => ({
  useWorkbenchStore: (sel: (s: { loadPins: typeof loadPins }) => unknown) =>
    sel({ loadPins }),
}));

import { usePins } from "@/lib/hooks/use-pins";

beforeEach(() => {
  loadPins.mockReset();
});

describe("usePins", () => {
  it("carrega os pins ao montar com threadId", () => {
    renderHook(() => usePins("t1"));
    expect(loadPins).toHaveBeenCalledWith("t1");
  });

  it("não carrega com threadId vazio", () => {
    renderHook(() => usePins(""));
    expect(loadPins).not.toHaveBeenCalled();
  });

  it("recarrega ao trocar de thread", () => {
    const { rerender } = renderHook(({ id }) => usePins(id), {
      initialProps: { id: "t1" },
    });
    expect(loadPins).toHaveBeenCalledWith("t1");
    rerender({ id: "t2" });
    expect(loadPins).toHaveBeenCalledWith("t2");
  });

  it("não recarrega se o threadId não muda entre renders", () => {
    const { rerender } = renderHook(({ id }) => usePins(id), {
      initialProps: { id: "t1" },
    });
    loadPins.mockClear();
    rerender({ id: "t1" });
    expect(loadPins).not.toHaveBeenCalled();
  });
});

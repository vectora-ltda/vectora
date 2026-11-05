// @vitest-environment jsdom
/**
 * Tests para useSwitchMode — alternar Chat/Dev abre sessão nova do modo.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";

const { mockNavigate, mockMarkAsNew, mockState } = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
  mockMarkAsNew: vi.fn(),
  mockState: { chatMode: false, setChatMode: vi.fn() },
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => mockNavigate,
}));
vi.mock("@/lib/stores/settings-store", () => ({
  useSettingsStore: (selector: (s: typeof mockState) => unknown) =>
    selector(mockState),
}));
vi.mock("@/lib/stores/new-thread-registry", () => ({
  markAsNew: mockMarkAsNew,
}));
vi.mock("@/lib/utils/uuid", () => ({
  safeRandomUUID: () => "new-id-123",
}));

import { useSwitchMode } from "../use-switch-mode";

beforeEach(() => {
  mockNavigate.mockReset();
  mockMarkAsNew.mockReset();
  mockState.setChatMode.mockReset();
  mockState.chatMode = false;
});

describe("useSwitchMode", () => {
  it("trocar de modo seta chatMode e abre sessão nova do modo", () => {
    mockState.chatMode = false; // dev → chat
    const { result } = renderHook(() => useSwitchMode());
    result.current(true);

    expect(mockState.setChatMode).toHaveBeenCalledWith(true);
    expect(mockMarkAsNew).toHaveBeenCalledWith("new-id-123");
    expect(mockNavigate).toHaveBeenCalledWith({
      to: "/session/$threadId",
      params: { threadId: "new-id-123" },
    });
  });

  it("erro/borda: já no modo → não abre sessão nova (sem navigate)", () => {
    mockState.chatMode = true; // já em chat, clicar em chat de novo
    const { result } = renderHook(() => useSwitchMode());
    result.current(true);

    expect(mockState.setChatMode).toHaveBeenCalledWith(true);
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(mockMarkAsNew).not.toHaveBeenCalled();
  });
});

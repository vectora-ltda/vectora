// @vitest-environment jsdom
/**
 * Tests para useThreadMessages: bridge useState-like sobre o threads-store,
 * com cache por threadId.
 */

import { describe, expect, it, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useThreadMessages } from "../use-thread-messages";
import { useThreadsStore } from "@/lib/stores/threads-store";
import type { Message } from "@/lib/types";

function m(id: string, content = "x"): Message {
  return { id, role: "user", content } as unknown as Message;
}

beforeEach(() => useThreadsStore.getState().clear());

describe("useThreadMessages", () => {
  it("começa vazio para thread sem cache", () => {
    const { result } = renderHook(() => useThreadMessages("t1"));
    expect(result.current[0]).toEqual([]);
  });

  it("setMessages com array substitui as mensagens", () => {
    const { result } = renderHook(() => useThreadMessages("t1"));
    act(() => result.current[1]([m("a"), m("b")]));
    expect(result.current[0]).toHaveLength(2);
  });

  it("setMessages com função faz patch sobre as atuais", () => {
    const { result } = renderHook(() => useThreadMessages("t1"));
    act(() => result.current[1]([m("a")]));
    act(() => result.current[1]((prev) => [...prev, m("b")]));
    expect(result.current[0].map((x) => x.id)).toEqual(["a", "b"]);
  });

  it("isola o cache por threadId", () => {
    const { result: r1 } = renderHook(() => useThreadMessages("t1"));
    const { result: r2 } = renderHook(() => useThreadMessages("t2"));
    act(() => r1.current[1]([m("a")]));
    expect(r1.current[0]).toHaveLength(1);
    expect(r2.current[0]).toHaveLength(0);
  });

  it("array vazio tem identidade estável (evita re-render)", () => {
    const { result, rerender } = renderHook(() => useThreadMessages("t1"));
    const first = result.current[0];
    rerender();
    expect(result.current[0]).toBe(first);
  });
});

// @vitest-environment jsdom
/**
 * Tests do useFeedback — gestão local de feedback (positivo/negativo, toggle,
 * comentário). Sem rede (os stubs de servidor foram removidos). Verifica a
 * aplicação otimista no array de mensagens e o estado de comentário.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import type { Message } from "@/lib/types";
import { useFeedback } from "../use-feedback";

function msg(over: Partial<Message> = {}): Message {
  return {
    id: "m1",
    role: "assistant",
    content: "resposta",
    timestamp: new Date(),
    runId: "run-1",
    ...over,
  } as Message;
}

describe("useFeedback", () => {
  let stored: Message[];
  const setMessages = (u: Message[] | ((p: Message[]) => Message[])) => {
    stored = typeof u === "function" ? u(stored) : u;
  };

  beforeEach(() => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  it("aplica feedback positivo na mensagem", async () => {
    const initial = [msg()];
    stored = [...initial];
    const { result } = renderHook(() =>
      useFeedback({ messages: initial, setMessages }),
    );
    await act(async () => {
      await result.current.handleFeedback("m1", "positive");
    });
    expect(stored[0].feedback).toBe("positive");
  });

  it("clicar no mesmo feedback faz toggle-off (remove)", async () => {
    const initial = [msg({ feedback: "positive" })];
    stored = [...initial];
    const { result } = renderHook(() =>
      useFeedback({ messages: initial, setMessages }),
    );
    await act(async () => {
      await result.current.handleFeedback("m1", "positive");
    });
    expect(stored[0].feedback).toBeNull();
  });

  it("sem runId não aplica feedback (no-op)", async () => {
    const initial = [msg({ runId: undefined })];
    stored = [...initial];
    const { result } = renderHook(() =>
      useFeedback({ messages: initial, setMessages }),
    );
    await act(async () => {
      await result.current.handleFeedback("m1", "negative");
    });
    expect(stored[0].feedback).toBeUndefined();
  });

  it("handleToggleComment abre o input e semeia o comentário", () => {
    const initial = [msg({ feedbackComment: "rascunho" })];
    stored = [...initial];
    const { result } = renderHook(() =>
      useFeedback({ messages: initial, setMessages }),
    );
    act(() => result.current.handleToggleComment("m1"));
    expect(result.current.showCommentInput).toBe("m1");
    expect(result.current.feedbackComment["m1"]).toBe("rascunho");
    // segundo toggle fecha
    act(() => result.current.handleToggleComment("m1"));
    expect(result.current.showCommentInput).toBeNull();
  });

  it("handleCancelComment limpa input e rascunho", () => {
    const initial = [msg()];
    stored = [...initial];
    const { result } = renderHook(() =>
      useFeedback({ messages: initial, setMessages }),
    );
    act(() => result.current.handleToggleComment("m1"));
    act(() => result.current.handleCancelComment("m1"));
    expect(result.current.showCommentInput).toBeNull();
    expect(result.current.feedbackComment["m1"]).toBeUndefined();
  });
});

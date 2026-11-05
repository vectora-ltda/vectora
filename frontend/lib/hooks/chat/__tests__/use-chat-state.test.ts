// @vitest-environment jsdom
/**
 * Tests para useChatState: input com rascunho por thread persistido no
 * chat-input-store, dispatch do reducer e clearInput.
 */

import { describe, expect, it, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useChatState } from "../use-chat-state";
import { useChatInputStore } from "@/lib/stores/chat-input-store";

beforeEach(() => {
  useChatInputStore.setState({ draft: null, mention: null, drafts: {} });
});

describe("useChatState", () => {
  it("inicia o input com o rascunho persistido da thread", () => {
    useChatInputStore.getState().setDraft("t1", "rascunho salvo");
    const { result } = renderHook(() => useChatState("t1"));
    expect(result.current.state.input).toBe("rascunho salvo");
  });

  it("setInput atualiza o estado e persiste no store", () => {
    const { result } = renderHook(() => useChatState("t1"));
    act(() => result.current.setInput("oi"));
    expect(result.current.state.input).toBe("oi");
    expect(useChatInputStore.getState().getDraft("t1")).toBe("oi");
  });

  it("setInput vazio remove o rascunho persistido", () => {
    const { result } = renderHook(() => useChatState("t1"));
    act(() => result.current.setInput("algo"));
    act(() => result.current.setInput(""));
    expect("t1" in useChatInputStore.getState().drafts).toBe(false);
  });

  it("clearInput zera o input e o rascunho", () => {
    const { result } = renderHook(() => useChatState("t1"));
    act(() => result.current.setInput("vai limpar"));
    act(() => result.current.clearInput());
    expect(result.current.state.input).toBe("");
    expect(useChatInputStore.getState().getDraft("t1")).toBe("");
  });

  it("START_SEND zera o input no reducer", () => {
    const { result } = renderHook(() => useChatState("t1"));
    act(() => result.current.setInput("mensagem"));
    act(() => result.current.dispatch({ type: "START_SEND" }));
    expect(result.current.state.input).toBe("");
    expect(result.current.state.isLoading).toBe(true);
  });

  it("threads diferentes têm rascunhos isolados", () => {
    useChatInputStore.getState().setDraft("t1", "A");
    useChatInputStore.getState().setDraft("t2", "B");
    const { result: r1 } = renderHook(() => useChatState("t1"));
    const { result: r2 } = renderHook(() => useChatState("t2"));
    expect(r1.current.state.input).toBe("A");
    expect(r2.current.state.input).toBe("B");
  });
});

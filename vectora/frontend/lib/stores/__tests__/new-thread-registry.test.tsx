// @vitest-environment jsdom
/**
 * Tests para o new-thread-registry: marca/limpa threads recém-criadas e
 * expira por TTL (5 min).
 */

import { describe, expect, it, afterEach, vi } from "vitest";
import { act, render } from "@testing-library/react";
import {
  markAsNew,
  isNew,
  clearNew,
  useIsNewThread,
} from "../new-thread-registry";

afterEach(() => {
  vi.useRealTimers();
  for (let i = 0; i < 10; i++) clearNew(`t${i}`);
  clearNew("t1");
  clearNew("t2");
  clearNew("reativa");
  clearNew("timer");
  clearNew("");
});

describe("new-thread-registry", () => {
  it("atualiza o hook quando o registro muda", () => {
    function Probe() {
      const value = useIsNewThread("reativa");
      return <output data-testid="state">{String(value)}</output>;
    }
    const view = render(<Probe />);
    expect(view.getByTestId("state").textContent).toBe("false");
    act(() => markAsNew("reativa"));
    expect(view.getByTestId("state").textContent).toBe("true");
    act(() => clearNew("reativa"));
    expect(view.getByTestId("state").textContent).toBe("false");
  });

  it("notifica o hook quando a marca expira", () => {
    vi.useFakeTimers();
    function Probe() {
      return (
        <output data-testid="state">{String(useIsNewThread("timer"))}</output>
      );
    }
    const view = render(<Probe />);
    act(() => markAsNew("timer"));
    expect(view.getByTestId("state").textContent).toBe("true");
    act(() => vi.advanceTimersByTime(5 * 60 * 1000 + 1));
    expect(view.getByTestId("state").textContent).toBe("false");
  });

  it("isNew é false para thread nunca marcada", () => {
    expect(isNew("desconhecida")).toBe(false);
  });

  it("markAsNew torna a thread 'nova' e clearNew remove", () => {
    markAsNew("t1");
    expect(isNew("t1")).toBe(true);
    clearNew("t1");
    expect(isNew("t1")).toBe(false);
  });

  it("expira a marcação após o TTL de 5 min", () => {
    vi.useFakeTimers();
    markAsNew("t2");
    expect(isNew("t2")).toBe(true);
    vi.advanceTimersByTime(5 * 60 * 1000 + 1);
    expect(isNew("t2")).toBe(false);
  });

  it("markAsNew duas vezes mantém a thread nova", () => {
    markAsNew("t3");
    markAsNew("t3");
    expect(isNew("t3")).toBe(true);
  });

  it("threads são independentes entre si", () => {
    markAsNew("t3");
    expect(isNew("t3")).toBe(true);
    expect(isNew("t4")).toBe(false);
  });

  it("clearNew de thread não marcada não quebra", () => {
    expect(() => clearNew("t9")).not.toThrow();
    expect(isNew("t9")).toBe(false);
  });

  it("não expira exatamente no limite do TTL", () => {
    vi.useFakeTimers();
    markAsNew("t3");
    vi.advanceTimersByTime(5 * 60 * 1000); // == TTL, não > TTL
    expect(isNew("t3")).toBe(true);
  });

  it("re-markAsNew reseta o relógio de expiração", () => {
    vi.useFakeTimers();
    markAsNew("t3");
    vi.advanceTimersByTime(4 * 60 * 1000); // 4 min
    markAsNew("t3"); // reseta createdAt
    vi.advanceTimersByTime(4 * 60 * 1000); // +4 min (8 total, mas só 4 desde o re-mark)
    expect(isNew("t3")).toBe(true);
  });

  it("clearNew de uma thread não afeta outra", () => {
    markAsNew("t3");
    markAsNew("t4");
    clearNew("t3");
    expect(isNew("t3")).toBe(false);
    expect(isNew("t4")).toBe(true);
  });

  it("suporta múltiplas threads novas simultâneas", () => {
    markAsNew("t3");
    markAsNew("t4");
    markAsNew("t5");
    expect(isNew("t3") && isNew("t4") && isNew("t5")).toBe(true);
  });

  it("aceita threadId string vazia", () => {
    markAsNew("");
    expect(isNew("")).toBe(true);
    clearNew("");
    expect(isNew("")).toBe(false);
  });

  it("atualiza o hook para uma thread com identificador vazio", () => {
    function Probe() {
      return (
        <output data-testid="empty-state">{String(useIsNewThread(""))}</output>
      );
    }
    const view = render(<Probe />);
    expect(view.getByTestId("empty-state").textContent).toBe("false");
    act(() => markAsNew(""));
    expect(view.getByTestId("empty-state").textContent).toBe("true");
    act(() => clearNew(""));
    expect(view.getByTestId("empty-state").textContent).toBe("false");
  });

  it("isNew após expiração remove a entrada internamente", () => {
    vi.useFakeTimers();
    markAsNew("t6");
    vi.advanceTimersByTime(5 * 60 * 1000 + 1);
    expect(isNew("t6")).toBe(false);
    // segunda chamada continua false (entrada já removida)
    expect(isNew("t6")).toBe(false);
  });
});

/**
 * Smoke tests para o `toast-store`. Asserts rodam em Node puro —
 * a store é Zustand sem dependência de DOM, então não precisamos de jsdom.
 */

import { describe, expect, it, beforeEach } from "vitest";
import { useToastStore } from "../toast-store";

beforeEach(() => {
  useToastStore.getState().clear();
});

describe("toast-store", () => {
  it("push devolve id e adiciona toast à fila", () => {
    const id = useToastStore.getState().success("Salvo!");
    expect(id).toBeTruthy();
    expect(useToastStore.getState().toasts).toHaveLength(1);
    expect(useToastStore.getState().toasts[0].level).toBe("success");
  });

  it("dedup ignora duplicatas pelo par (level, title)", () => {
    const id1 = useToastStore.getState().error("Falha de rede");
    const id2 = useToastStore.getState().error("Falha de rede");
    expect(id1).toBe(id2);
    expect(useToastStore.getState().toasts).toHaveLength(1);
  });

  it("dedup considera level: mesmo title em níveis diferentes coexiste", () => {
    useToastStore.getState().error("X");
    useToastStore.getState().success("X");
    expect(useToastStore.getState().toasts).toHaveLength(2);
  });

  it("aplica cap MAX_TOASTS=3 descartando o mais antigo", () => {
    const store = useToastStore.getState();
    store.info("A");
    store.info("B");
    store.info("C");
    store.info("D");
    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(3);
    expect(toasts.map((t) => t.title)).toEqual(["B", "C", "D"]);
  });

  it("erro tem duration null (não auto-dismiss)", () => {
    useToastStore.getState().error("Oops");
    expect(useToastStore.getState().toasts[0].duration).toBeNull();
  });

  it("success/info têm duration default 4000ms", () => {
    useToastStore.getState().success("Ok");
    expect(useToastStore.getState().toasts[0].duration).toBe(4000);
  });

  it("warning tem duration default 6000ms", () => {
    useToastStore.getState().warning("Atenção");
    expect(useToastStore.getState().toasts[0].duration).toBe(6000);
  });

  it("dismiss remove apenas o id alvo", () => {
    const a = useToastStore.getState().info("A");
    useToastStore.getState().info("B");
    useToastStore.getState().dismiss(a);
    expect(useToastStore.getState().toasts).toHaveLength(1);
    expect(useToastStore.getState().toasts[0].title).toBe("B");
  });

  it("clear esvazia a fila", () => {
    useToastStore.getState().info("A");
    useToastStore.getState().info("B");
    useToastStore.getState().clear();
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it("push aceita override explícito de duration", () => {
    useToastStore.getState().push({
      level: "success",
      title: "Done",
      duration: 1000,
    });
    expect(useToastStore.getState().toasts[0].duration).toBe(1000);
  });

  it("push aceita duration=null para success (force sticky)", () => {
    useToastStore.getState().push({
      level: "success",
      title: "Done",
      duration: null,
    });
    expect(useToastStore.getState().toasts[0].duration).toBeNull();
  });
});

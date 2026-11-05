/**
 * Tests para o dialog-size-store: persiste o tamanho redimensionado de cada
 * diálogo por chave, com fallback nos defaults.
 */

import { describe, expect, it, beforeEach } from "vitest";
import { useDialogSizeStore } from "../dialog-size-store";

beforeEach(() => {
  useDialogSizeStore.setState({ sizes: {} });
  if (typeof localStorage !== "undefined") localStorage.clear();
});

describe("dialog-size-store", () => {
  it("getSize devolve os defaults quando a chave não foi salva", () => {
    const def = { w: 600, h: 460 };
    expect(useDialogSizeStore.getState().getSize("preferencias", def)).toBe(
      def,
    );
  });

  it("setSize persiste o tamanho e getSize o devolve", () => {
    useDialogSizeStore.getState().setSize("preferencias", { w: 800, h: 500 });
    expect(
      useDialogSizeStore.getState().getSize("preferencias", { w: 1, h: 1 }),
    ).toEqual({ w: 800, h: 500 });
  });

  it("isola tamanhos por chave de diálogo", () => {
    const s = useDialogSizeStore.getState();
    s.setSize("preferencias", { w: 800, h: 500 });
    s.setSize("administracao", { w: 900, h: 600 });
    expect(useDialogSizeStore.getState().sizes.preferencias).toEqual({
      w: 800,
      h: 500,
    });
    expect(useDialogSizeStore.getState().sizes.administracao).toEqual({
      w: 900,
      h: 600,
    });
  });

  it("sizes começa vazio", () => {
    expect(useDialogSizeStore.getState().sizes).toEqual({});
  });

  it("setSize sobrescreve a mesma chave", () => {
    const s = useDialogSizeStore.getState();
    s.setSize("k", { w: 100, h: 100 });
    s.setSize("k", { w: 200, h: 150 });
    expect(useDialogSizeStore.getState().sizes.k).toEqual({ w: 200, h: 150 });
  });

  it("setSize preserva outras chaves já salvas", () => {
    const s = useDialogSizeStore.getState();
    s.setSize("a", { w: 10, h: 10 });
    s.setSize("b", { w: 20, h: 20 });
    expect(useDialogSizeStore.getState().sizes.a).toEqual({ w: 10, h: 10 });
  });

  it("getSize devolve o salvo em vez dos defaults", () => {
    useDialogSizeStore.getState().setSize("k", { w: 800, h: 500 });
    expect(
      useDialogSizeStore.getState().getSize("k", { w: 1, h: 1 }),
    ).not.toEqual({ w: 1, h: 1 });
  });

  it("getSize usa defaults distintos por chave", () => {
    const s = useDialogSizeStore.getState();
    expect(s.getSize("x", { w: 300, h: 200 })).toEqual({ w: 300, h: 200 });
    expect(s.getSize("y", { w: 640, h: 480 })).toEqual({ w: 640, h: 480 });
  });

  it("getSize não muta o mapa de sizes", () => {
    const s = useDialogSizeStore.getState();
    s.getSize("nope", { w: 1, h: 2 });
    expect(useDialogSizeStore.getState().sizes).toEqual({});
  });

  it("aceita w/h zero", () => {
    useDialogSizeStore.getState().setSize("k", { w: 0, h: 0 });
    expect(useDialogSizeStore.getState().sizes.k).toEqual({ w: 0, h: 0 });
  });

  it("aceita dimensões grandes", () => {
    useDialogSizeStore.getState().setSize("k", { w: 4000, h: 3000 });
    expect(useDialogSizeStore.getState().getSize("k", { w: 1, h: 1 })).toEqual({
      w: 4000,
      h: 3000,
    });
  });

  it("aceita dimensões fracionárias", () => {
    useDialogSizeStore.getState().setSize("k", { w: 100.5, h: 50.25 });
    expect(useDialogSizeStore.getState().sizes.k).toEqual({
      w: 100.5,
      h: 50.25,
    });
  });

  it("chaves diferentes não colidem após múltiplos sets", () => {
    const s = useDialogSizeStore.getState();
    for (let i = 0; i < 5; i++) s.setSize(`k${i}`, { w: i, h: i * 2 });
    expect(Object.keys(useDialogSizeStore.getState().sizes)).toHaveLength(5);
    expect(useDialogSizeStore.getState().sizes.k3).toEqual({ w: 3, h: 6 });
  });

  it("getSize de chave não salva devolve a mesma referência de defaults", () => {
    const def = { w: 600, h: 460 };
    expect(useDialogSizeStore.getState().getSize("ghost", def)).toBe(def);
  });
});

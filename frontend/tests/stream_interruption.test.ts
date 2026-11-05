/**
 * lib/utils/stream-interruption.ts
 * Cobre markStreamStarted, markStreamEnded, consumeInterruptedFlag.
 * Usa localStorage mock do vitest (jsdom).
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  markStreamStarted,
  markStreamEnded,
  consumeInterruptedFlag,
} from "../lib/utils/stream-interruption";

// stream-interruption usa window.localStorage; simulamos com Map no ambiente node.
function makeLocalStorage() {
  const store = new Map<string, string>();
  return {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => {
      store.set(k, v);
    },
    removeItem: (k: string) => {
      store.delete(k);
    },
    clear: () => store.clear(),
  };
}

const fakeStorage = makeLocalStorage();

beforeEach(() => {
  fakeStorage.clear();
  vi.stubGlobal("window", { localStorage: fakeStorage });
});

describe("stream-interruption", () => {
  it("sem marca → consumeInterruptedFlag retorna false", () => {
    expect(consumeInterruptedFlag("t1")).toBe(false);
  });

  it("markStreamStarted → consumeInterruptedFlag retorna true (e remove marca)", () => {
    markStreamStarted("t1");
    expect(consumeInterruptedFlag("t1")).toBe(true);
    // Consumido: segunda chamada deve retornar false
    expect(consumeInterruptedFlag("t1")).toBe(false);
  });

  it("markStreamEnded após started → consumeInterruptedFlag retorna false", () => {
    markStreamStarted("t2");
    markStreamEnded("t2");
    expect(consumeInterruptedFlag("t2")).toBe(false);
  });

  it("threads independentes não interferem entre si", () => {
    markStreamStarted("tA");
    markStreamStarted("tB");
    markStreamEnded("tA");
    expect(consumeInterruptedFlag("tA")).toBe(false);
    expect(consumeInterruptedFlag("tB")).toBe(true);
  });

  it("marca expirada (> 30min) → retorna false", () => {
    const staleTs = Date.now() - 31 * 60 * 1000;
    fakeStorage.setItem("vectora:streaming:old", String(staleTs));
    expect(consumeInterruptedFlag("old")).toBe(false);
  });

  it("marca recente (< 30min) → retorna true", () => {
    const recentTs = Date.now() - 5 * 60 * 1000;
    fakeStorage.setItem("vectora:streaming:recent", String(recentTs));
    expect(consumeInterruptedFlag("recent")).toBe(true);
  });
});

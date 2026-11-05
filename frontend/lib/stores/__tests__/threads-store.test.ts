/**
 * Tests para o `threads-store`: cache de mensagens por thread + GC
 * (TTL / cap / pressão de memória). Lógica pura, sem DOM.
 */

import { describe, expect, it, beforeEach } from "vitest";
import { useThreadsStore, MESSAGES_IN_MEMORY_CAP } from "../threads-store";
import type { Message } from "@/lib/types";

function msg(id: string, content = "oi"): Message {
  return { id, role: "user", content } as unknown as Message;
}

beforeEach(() => {
  useThreadsStore.getState().clear();
});

describe("threads-store — cache", () => {
  it("setMessages popula e getCached devolve as mensagens", () => {
    useThreadsStore.getState().setMessages("t1", [msg("a"), msg("b")]);
    const entry = useThreadsStore.getState().getCached("t1");
    expect(entry?.messages).toHaveLength(2);
    expect(entry?.sizeBytes).toBeGreaterThan(0);
  });

  it("getCached devolve undefined para thread nunca vista", () => {
    expect(useThreadsStore.getState().getCached("nope")).toBeUndefined();
  });

  it("patchMessages aplica updater sobre o cache atual", () => {
    useThreadsStore.getState().setMessages("t1", [msg("a")]);
    useThreadsStore.getState().patchMessages("t1", (cur) => [...cur, msg("b")]);
    expect(useThreadsStore.getState().getCached("t1")?.messages).toHaveLength(
      2,
    );
  });

  it("patchMessages preserva identidade se updater devolve o mesmo array", () => {
    useThreadsStore.getState().setMessages("t1", [msg("a")]);
    const before = useThreadsStore.getState().cache;
    useThreadsStore.getState().patchMessages("t1", (cur) => cur);
    expect(useThreadsStore.getState().cache).toBe(before);
  });

  it("invalidate remove a thread do cache", () => {
    useThreadsStore.getState().setMessages("t1", [msg("a")]);
    useThreadsStore.getState().invalidate("t1");
    expect(useThreadsStore.getState().getCached("t1")).toBeUndefined();
  });

  it("clear esvazia todo o cache", () => {
    useThreadsStore.getState().setMessages("t1", [msg("a")]);
    useThreadsStore.getState().setMessages("t2", [msg("b")]);
    useThreadsStore.getState().clear();
    expect(Object.keys(useThreadsStore.getState().cache)).toHaveLength(0);
  });

  it("setRevalidating marca e desmarca a flag por thread", () => {
    useThreadsStore.getState().setRevalidating("t1", true);
    expect(useThreadsStore.getState().revalidating["t1"]).toBe(true);
    useThreadsStore.getState().setRevalidating("t1", false);
    expect("t1" in useThreadsStore.getState().revalidating).toBe(false);
  });
});

describe("threads-store — GC", () => {
  it("respeita o cap de entradas (LRU por updatedAt)", () => {
    for (let i = 0; i < MESSAGES_IN_MEMORY_CAP + 10; i++) {
      useThreadsStore.getState().setMessages(`t${i}`, [msg(`m${i}`)]);
    }
    expect(
      Object.keys(useThreadsStore.getState().cache).length,
    ).toBeLessThanOrEqual(MESSAGES_IN_MEMORY_CAP);
  });

  it("gcCache remove entradas expiradas por TTL", () => {
    useThreadsStore.getState().setMessages("fresh", [msg("a")]);
    // Injeta uma entrada antiga (updatedAt muito no passado) e roda o GC.
    useThreadsStore.setState((s) => ({
      cache: {
        ...s.cache,
        stale: {
          messages: [msg("old")],
          fetchedAt: 0,
          updatedAt: Date.now() - 60 * 60 * 1000, // 1h atrás
          sizeBytes: 10,
        },
      },
    }));
    useThreadsStore.getState().gcCache();
    expect(useThreadsStore.getState().getCached("stale")).toBeUndefined();
    expect(useThreadsStore.getState().getCached("fresh")).toBeDefined();
  });
});

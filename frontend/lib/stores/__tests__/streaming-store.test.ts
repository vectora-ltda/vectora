/**
 * Tests para o streaming-store: quais threads têm stream SSE ativo,
 * simultaneamente (mapa por threadId, não um único valor global).
 */

import { describe, it, expect, beforeEach } from "vitest";
import { useStreamingStore } from "../streaming-store";

beforeEach(() => {
  useStreamingStore.setState({ streaming: {} });
});

describe("streaming-store", () => {
  it("nenhuma thread está streaming inicialmente", () => {
    expect(useStreamingStore.getState().streaming).toEqual({});
    expect(useStreamingStore.getState().isStreaming("t1")).toBe(false);
  });

  it("setStreaming(id, true) marca a thread como streaming", () => {
    useStreamingStore.getState().setStreaming("t1", true);
    expect(useStreamingStore.getState().isStreaming("t1")).toBe(true);
  });

  it("setStreaming(id, false) desmarca só aquela thread", () => {
    const s = useStreamingStore.getState();
    s.setStreaming("t1", true);
    s.setStreaming("t1", false);
    expect(useStreamingStore.getState().isStreaming("t1")).toBe(false);
  });

  it("duas threads streamando ao mesmo tempo — regressão do bug de sessão fantasma: navegar pra outra thread e mandar mensagem lá não pode apagar o indicador da 1ª thread, que ainda está com stream em andamento", () => {
    const s = useStreamingStore.getState();
    s.setStreaming("t1", true);
    s.setStreaming("t2", true);
    expect(useStreamingStore.getState().isStreaming("t1")).toBe(true);
    expect(useStreamingStore.getState().isStreaming("t2")).toBe(true);

    s.setStreaming("t1", false);
    expect(useStreamingStore.getState().isStreaming("t1")).toBe(false);
    expect(useStreamingStore.getState().isStreaming("t2")).toBe(true);
  });

  it("setStreaming(id, true) repetido é idempotente", () => {
    const s = useStreamingStore.getState();
    s.setStreaming("t1", true);
    s.setStreaming("t1", true);
    expect(useStreamingStore.getState().isStreaming("t1")).toBe(true);
    expect(Object.keys(useStreamingStore.getState().streaming)).toEqual(["t1"]);
  });

  it("ids longos (uuid) são preservados", () => {
    const uuid = "550e8400-e29b-41d4-a716-446655440000";
    useStreamingStore.getState().setStreaming(uuid, true);
    expect(useStreamingStore.getState().isStreaming(uuid)).toBe(true);
  });

  it("id vazio é ignorado (nunca deve virar chave no mapa)", () => {
    useStreamingStore.getState().setStreaming("", true);
    expect(useStreamingStore.getState().streaming).toEqual({});
  });
});

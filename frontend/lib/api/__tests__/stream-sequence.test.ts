import { describe, expect, it } from "vitest";
import { createSequenceGuard } from "../stream-sequence";

describe("createSequenceGuard", () => {
  it("aceita eventos em ordem e avança o cursor", () => {
    const guard = createSequenceGuard();
    expect(guard.accept({ type: "thread", seq: 1 })).toBe(true);
    expect(guard.accept({ type: "token", seq: 2 })).toBe(true);
    expect(guard.cursor()).toBe(2);
  });

  it("descarta evento repetido e evento reordenado", () => {
    const guard = createSequenceGuard();
    guard.accept({ type: "token", seq: 1 });
    guard.accept({ type: "token", seq: 2 });
    expect(guard.accept({ type: "token", seq: 2 })).toBe(false);
    expect(guard.accept({ type: "token", seq: 1 })).toBe(false);
    expect(guard.accept({ type: "token", seq: 3 })).toBe(true);
    expect(guard.cursor()).toBe(3);
  });

  it("aceita lacuna na sequência (o terminal e o estado persistido reconciliam)", () => {
    const guard = createSequenceGuard();
    guard.accept({ type: "token", seq: 1 });
    expect(guard.accept({ type: "token", seq: 5 })).toBe(true);
  });

  it("depois do done ignora tudo, inclusive retransmissão atrasada", () => {
    const guard = createSequenceGuard();
    guard.accept({ type: "token", seq: 1 });
    expect(guard.accept({ type: "done", seq: 2 })).toBe(true);
    expect(guard.finished()).toBe(true);
    expect(guard.accept({ type: "token", seq: 3 })).toBe(false);
    expect(guard.accept({ type: "thread", seq: 9 })).toBe(false);
  });

  it("não trata error como terminal: o done ainda chega", () => {
    const guard = createSequenceGuard();
    expect(guard.accept({ type: "error", seq: 1 })).toBe(true);
    expect(guard.finished()).toBe(false);
    expect(guard.accept({ type: "done", seq: 2 })).toBe(true);
  });

  it("backend sem seq: passa tudo até o done; event_id repetido é descartado", () => {
    const guard = createSequenceGuard();
    expect(guard.accept({ type: "token" })).toBe(true);
    expect(guard.accept({ type: "token" })).toBe(true);
    expect(guard.cursor()).toBe(0);
    expect(guard.accept({ type: "token", event_id: "r:1" })).toBe(true);
    expect(guard.accept({ type: "token", event_id: "r:1" })).toBe(false);
    expect(guard.accept({ type: "done" })).toBe(true);
    expect(guard.accept({ type: "token" })).toBe(false);
  });
});

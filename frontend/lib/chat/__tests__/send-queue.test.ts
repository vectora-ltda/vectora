import { describe, expect, it } from "vitest";
import { createSendQueue } from "../send-queue";

function queueWith(...ids: string[]) {
  const queue = createSendQueue<string>();
  for (const id of ids) queue.enqueue(id, `msg-${id}`);
  return queue;
}

describe("createSendQueue", () => {
  it("enfileira em ordem e não duplica o mesmo ID", () => {
    const queue = queueWith("a", "b");
    expect(queue.enqueue("a", "outra")).toBe(false);
    expect(queue.items().map((item) => item.id)).toEqual(["a", "b"]);
    expect(queue.items()[0].payload).toBe("msg-a");
  });

  it("envia um de cada vez e remove só quando o servidor confirma", () => {
    const queue = queueWith("a", "b");
    expect(queue.next()?.id).toBe("a");
    expect(queue.start("a")).toBe(true);
    expect(queue.items()[0].status).toBe("sending");
    expect(queue.next()).toBeUndefined();
    expect(queue.succeed("a")).toBe(true);
    expect(queue.next()?.id).toBe("b");
  });

  it("falha preserva o item, bloqueia os seguintes e o retry mantém ID e posição", () => {
    const queue = queueWith("a", "b");
    queue.start("a");
    expect(queue.fail("a")).toBe(true);
    expect(queue.items()[0]).toMatchObject({ id: "a", status: "failed" });
    expect(queue.next()).toBeUndefined();
    expect(queue.retry("a")).toBe(true);
    expect(queue.next()?.id).toBe("a");
    expect(queue.items().map((item) => item.id)).toEqual(["a", "b"]);
  });

  it("remover libera a fila bloqueada por uma falha, mas não remove item em envio", () => {
    const queue = queueWith("a", "b");
    queue.start("a");
    expect(queue.remove("a")).toBe(false);
    queue.fail("a");
    expect(queue.remove("a")).toBe(true);
    expect(queue.next()?.id).toBe("b");
  });

  it("promover move um item aguardando para a frente", () => {
    const queue = queueWith("a", "b", "c");
    expect(queue.promote("c")).toBe(true);
    expect(queue.items().map((item) => item.id)).toEqual(["c", "a", "b"]);
    queue.start("c");
    expect(queue.promote("a")).toBe(true);
    expect(queue.items().map((item) => item.id)).toEqual(["a", "c", "b"]);
    expect(queue.promote("c")).toBe(false);
  });

  it("transições inválidas não alteram nada", () => {
    const queue = queueWith("a");
    expect(queue.fail("a")).toBe(false);
    expect(queue.retry("a")).toBe(false);
    expect(queue.start("zzz")).toBe(false);
    expect(queue.succeed("zzz")).toBe(false);
    expect(queue.items()[0].status).toBe("waiting");
  });
});

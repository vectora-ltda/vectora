import { describe, expect, it, vi } from "vitest";
import { withResume, type ResumableEvent } from "../resumable-stream";

const ev = (seq: number, type = "token"): ResumableEvent => ({
  type,
  event_id: `run-1:${seq}`,
});

async function* from(
  events: ResumableEvent[],
  fail?: Error,
): AsyncGenerator<ResumableEvent> {
  for (const event of events) yield event;
  if (fail) throw fail;
}

async function collect(stream: AsyncGenerator<ResumableEvent>) {
  const out: string[] = [];
  for await (const event of stream) out.push(event.event_id ?? event.type);
  return out;
}

const noDelay = () => 0;
const never = () => false;

describe("withResume", () => {
  it("não reabre nada quando o stream chega ao done", async () => {
    const open = vi.fn();
    const out = await collect(
      withResume(from([ev(1), ev(2, "done")]), {
        open,
        isUnavailable: never,
        delayMs: noDelay,
      }),
    );
    expect(out).toEqual(["run-1:1", "run-1:2"]);
    expect(open).not.toHaveBeenCalled();
  });

  it("retoma do último event_id quando o stream acaba sem done", async () => {
    const open = vi.fn(() => from([ev(3), ev(4, "done")]));
    const out = await collect(
      withResume(from([ev(1), ev(2)]), {
        open,
        isUnavailable: never,
        delayMs: noDelay,
      }),
    );
    expect(open).toHaveBeenCalledWith("run-1:2", undefined);
    expect(out).toEqual(["run-1:1", "run-1:2", "run-1:3", "run-1:4"]);
  });

  it("retoma depois de uma falha de transporte", async () => {
    const open = vi.fn(() => from([ev(2, "done")]));
    const out = await collect(
      withResume(from([ev(1)], new Error("network")), {
        open,
        isUnavailable: never,
        delayMs: noDelay,
      }),
    );
    expect(out).toEqual(["run-1:1", "run-1:2"]);
  });

  it("tenta de novo, a partir do cursor mais recente, se a retomada também cair", async () => {
    const open = vi
      .fn()
      .mockImplementationOnce(() => from([ev(2)], new Error("caiu de novo")))
      .mockImplementationOnce(() => from([ev(3, "done")]));
    const out = await collect(
      withResume(from([ev(1)]), {
        open,
        isUnavailable: never,
        delayMs: noDelay,
      }),
    );
    expect(open.mock.calls.map((call) => call[0])).toEqual([
      "run-1:1",
      "run-1:2",
    ]);
    expect(out).toEqual(["run-1:1", "run-1:2", "run-1:3"]);
  });

  it("servidor sem os eventos: fim silencioso termina e falha de transporte é relançada", async () => {
    const unavailable = new Error("indisponível");
    const options = {
      open: () => from([], unavailable),
      isUnavailable: (error: unknown) => error === unavailable,
      delayMs: noDelay,
    };
    expect(await collect(withResume(from([ev(1)]), options))).toEqual([
      "run-1:1",
    ]);
    await expect(
      collect(withResume(from([ev(1)], new Error("network")), options)),
    ).rejects.toThrow("network");
  });

  it("sem event_id (backend antigo) não tenta retomar", async () => {
    const open = vi.fn();
    const out = await collect(
      withResume(from([{ type: "token" }]), {
        open,
        isUnavailable: never,
        delayMs: noDelay,
      }),
    );
    expect(out).toEqual(["token"]);
    expect(open).not.toHaveBeenCalled();
  });

  it("desiste depois de maxAttempts e propaga a falha", async () => {
    const open = vi.fn(() => from([], new Error("sem rede")));
    await expect(
      collect(
        withResume(from([ev(1)], new Error("network")), {
          open,
          isUnavailable: never,
          delayMs: noDelay,
          maxAttempts: 2,
        }),
      ),
    ).rejects.toThrow();
    expect(open).toHaveBeenCalledTimes(2);
  });

  it("abort do usuário não dispara retomada", async () => {
    const controller = new AbortController();
    controller.abort();
    const open = vi.fn();
    const abortError = Object.assign(new Error("abort"), {
      name: "AbortError",
    });
    await expect(
      collect(
        withResume(from([ev(1)], abortError), {
          open,
          isUnavailable: never,
          signal: controller.signal,
          delayMs: noDelay,
        }),
      ),
    ).rejects.toThrow("abort");
    expect(open).not.toHaveBeenCalled();
  });
});

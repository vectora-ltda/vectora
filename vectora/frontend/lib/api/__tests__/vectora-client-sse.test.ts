// @vitest-environment jsdom
/**
 * Testes do parser SSE interno (readSSEStream, via streamChat) — cobre o bug
 * real em que o evento final (ex.: `done`) é descartado quando a conexão
 * fecha (`reader.read()` retorna `done: true`) exatamente no meio de uma
 * linha `data: {...}` que nunca teve seu `\n\n` terminador entregue antes
 * do fechamento do socket.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  resumeChat,
  revokeCurrentDevice,
  streamChat,
} from "@/lib/api/vectora-client";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

function encode(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

/**
 * Constrói um ReadableStream cujos reads são controlados manualmente pela
 * lista `chunks` — o último `read()` sempre retorna `done: true` sem
 * `value`, simulando o socket fechando logo após o penúltimo chunk, sem
 * mais nenhum byte (nem `\n`) ser entregue depois dele.
 */
function makeControlledStream(chunks: string[]): ReadableStream<Uint8Array> {
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) {
        controller.enqueue(encode(chunks[i]));
        i += 1;
      } else {
        controller.close();
      }
    },
  });
}

function okResponse(body: ReadableStream<Uint8Array>): Response {
  return {
    ok: true,
    status: 200,
    body,
  } as unknown as Response;
}

async function collect<T>(gen: AsyncGenerator<T>): Promise<T[]> {
  const out: T[] = [];
  for await (const item of gen) out.push(item);
  return out;
}

describe("readSSEStream (via streamChat) — evento final sem \\n\\n terminador", () => {
  it("entrega o evento 'done' mesmo quando ele chega no último chunk sem \\n\\n final", async () => {
    // 1º chunk: 1 evento completo (com \n\n). 2º chunk: o evento `done`,
    // SEM o \n\n de fechamento — o socket "fecha" logo em seguida (o
    // próximo read() do stream fará controller.close(), produzindo
    // done:true sem value adicional).
    const chunk1 = `data: ${JSON.stringify({ type: "token", content: "hello" })}\n\n`;
    const chunk2 = `data: ${JSON.stringify({ type: "done", thread_id: "t1", run_id: "r1" })}`;

    fetchMock.mockResolvedValueOnce(
      okResponse(makeControlledStream([chunk1, chunk2])),
    );

    const events = await collect(
      streamChat({ thread_id: "t1", content: "oi" }),
    );

    expect(events).toEqual([
      { type: "token", content: "hello" },
      { type: "done", thread_id: "t1", run_id: "r1" },
    ]);
  });

  it("não perde tokens quando o corte acontece no meio do texto de um token", async () => {
    const chunk1 = `data: ${JSON.stringify({ type: "token", content: "parte" })}`;

    fetchMock.mockResolvedValueOnce(okResponse(makeControlledStream([chunk1])));

    const events = await collect(
      streamChat({ thread_id: "t1", content: "oi" }),
    );

    expect(events).toEqual([{ type: "token", content: "parte" }]);
  });

  it("ignora buffer residual vazio/whitespace sem gerar evento fantasma", async () => {
    const chunk1 = `data: ${JSON.stringify({ type: "token", content: "x" })}\n\n`;

    fetchMock.mockResolvedValueOnce(
      okResponse(makeControlledStream([chunk1, "   "])),
    );

    const events = await collect(
      streamChat({ thread_id: "t1", content: "oi" }),
    );

    expect(events).toEqual([{ type: "token", content: "x" }]);
  });
});

describe("resumeChat device activity", () => {
  it("envia o identificador opaco do dispositivo no stream de retomada", async () => {
    window.localStorage.setItem(
      "vectora-device-id",
      "vdev_12345678-1234-1234-1234-123456789abc",
    );
    fetchMock.mockResolvedValueOnce(
      okResponse(makeControlledStream(['data: {"type":"done"}\n\n'])),
    );

    await collect(
      resumeChat({ thread_id: "t1", interrupt_id: "i1", decision: "approve" }),
    );

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("ResumeChat"),
      expect.objectContaining({
        headers: {
          "Content-Type": "application/json",
          "X-Vectora-Device-Id": "vdev_12345678-1234-1234-1234-123456789abc",
        },
      }),
    );
  });
});

describe("revokeCurrentDevice", () => {
  it("envia o device id, remove a atividade remota e gira o id local", async () => {
    window.localStorage.setItem(
      "vectora-device-id",
      "vdev_12345678-1234-1234-1234-123456789abc",
    );
    fetchMock.mockResolvedValueOnce({ ok: true, status: 204 });

    await revokeCurrentDevice();

    expect(fetchMock).toHaveBeenCalledWith(
      "/threads/device/revoke",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: {
          Accept: "application/json",
          "X-Vectora-Device-Id": "vdev_12345678-1234-1234-1234-123456789abc",
        },
      }),
    );
    expect(window.localStorage.getItem("vectora-device-id")).toBeNull();
  });
});

// @vitest-environment jsdom
/**
 * RPCs de thread sem cobertura: getThread, deleteThread, updateThread,
 * transcribeAudio.
 *
 * O foco é o que cada um coloca no BODY — `updateThread` monta o payload
 * condicionalmente (campo ausente não pode virar `undefined` no JSON, o que
 * apagaria o valor no backend).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  getThread,
  deleteThread,
  updateThread,
  transcribeAudio,
  cancelRun,
} from "@/lib/api/vectora-client";

function jsonResponse(data: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data),
  } as Response;
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

/** Body do último POST, já desserializado. */
function lastBody(): Record<string, unknown> {
  const init = fetchMock.mock.calls.at(-1)?.[1] as RequestInit;
  return JSON.parse(String(init.body)) as Record<string, unknown>;
}

describe("cancelRun", () => {
  it("cancela o run do servidor pela thread e devolve o resultado", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ cancelled: true }));

    await expect(cancelRun("thread-1")).resolves.toBe(true);

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      "ChatService/CancelRun",
    );
    expect(lastBody()).toEqual({ thread_id: "thread-1" });
  });

  it("erro/borda: propaga falha HTTP em vez de dizer que cancelou", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "não autorizado" }, 404),
    );

    await expect(cancelRun("thread-de-outra-pessoa")).rejects.toThrow(/404/);
  });
});

describe("RPCs simples de thread", () => {
  it("getThread envia thread_id e devolve a thread", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: "t1", title: "Olá" }));
    const t = await getThread("t1");

    expect(lastBody()).toEqual({ thread_id: "t1" });
    expect(t.id).toBe("t1");
  });

  it("deleteThread envia thread_id", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}));
    await deleteThread("t2");

    expect(lastBody()).toEqual({ thread_id: "t2" });
  });

  it("erro/borda: getThread propaga o status quando a thread não existe", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "não achei" }, 404));
    await expect(getThread("sumida")).rejects.toThrow(/404/);
  });
});

describe("updateThread — payload condicional", () => {
  it("só title: `pinned` não entra no body", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: "t1" }));
    await updateThread("t1", { title: "Novo" });

    expect(lastBody()).toEqual({ thread_id: "t1", title: "Novo" });
  });

  it("só pinned: `title` não entra no body", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: "t1" }));
    await updateThread("t1", { pinned: true });

    expect(lastBody()).toEqual({ thread_id: "t1", pinned: true });
  });

  it("erro/borda: updates vazio manda só o id — nunca campos undefined", async () => {
    // Mandar `title: undefined` viraria ausência no JSON, mas mandar `null`
    // apagaria o título no backend. O contrato é omitir de verdade.
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: "t1" }));
    await updateThread("t1", {});

    const body = lastBody();
    expect(body).toEqual({ thread_id: "t1" });
    expect("title" in body).toBe(false);
    expect("pinned" in body).toBe(false);
  });

  it("título vazio é valor legítimo e vai no body (não é tratado como ausente)", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: "t1" }));
    await updateThread("t1", { title: "" });

    expect(lastBody()).toEqual({ thread_id: "t1", title: "" });
  });
});

describe("transcribeAudio", () => {
  it("envia áudio, mime e usa o filename default quando omitido", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ text: "olá mundo" }));
    const r = await transcribeAudio("YmFzZTY0", "audio/webm");

    expect(lastBody()).toEqual({
      audio_base64: "YmFzZTY0",
      mime_type: "audio/webm",
      filename: "recording.webm",
      transcription_mode: "remote",
    });
    expect(r.text).toBe("olá mundo");
  });

  it("filename explícito sobrepõe o default", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ text: "" }));
    await transcribeAudio("x", "audio/mp4", "ditado.m4a");

    expect(lastBody().filename).toBe("ditado.m4a");
  });

  it("envia modo local explicitamente quando selecionado", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ text: "ditado local" }));
    const result = await transcribeAudio(
      "x",
      "audio/webm",
      "voz.webm",
      "local",
    );

    expect(lastBody()).toEqual({
      audio_base64: "x",
      mime_type: "audio/webm",
      filename: "voz.webm",
      transcription_mode: "local",
    });
    expect(result.text).toBe("ditado local");
  });
});

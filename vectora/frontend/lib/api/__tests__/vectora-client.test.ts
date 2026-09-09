// @vitest-environment jsdom
/**
 * Tests dos RPCs do vectora-client (mock de `fetch`): serialização do body,
 * credentials, parse da resposta, refresh automático em 401 e erro tipado.
 * Cobre os RPCs novos (generateTitle) e o caminho de auth compartilhado.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  getHistory,
  generateTitle,
  listThreads,
  createThread,
  getThreadPins,
  setThreadPins,
  updateThread,
  markThreadRead,
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

describe("RPCs simples", () => {
  it("markThreadRead: faz POST com id codificado e credenciais", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ unread_count: 0 }));

    await markThreadRead("thread com espaço/");

    const [url, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain(
      "/threads/thread%20com%20espa%C3%A7o%2F/read",
    );
    expect(opts.method).toBe("POST");
    expect(opts.credentials).toBe("include");
  });

  it("generateTitle: POST GenerateTitle com thread_id e retorna {title}", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ title: "Plano de deploy" }));
    const r = await generateTitle("t1");
    expect(r.title).toBe("Plano de deploy");

    const [url, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain(
      "/vectora.chat.v1.ThreadService/GenerateTitle",
    );
    expect(opts.method).toBe("POST");
    expect(opts.credentials).toBe("include");
    expect(JSON.parse(opts.body as string)).toEqual({ thread_id: "t1" });
  });

  it("getHistory: retorna a lista de mensagens do backend", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ messages: [{ role: "human", content: "oi" }] }),
    );
    const r = await getHistory("t1");
    expect(r.messages).toHaveLength(1);
    expect(r.messages[0]).toEqual({ role: "human", content: "oi" });
  });

  it("createThread: envia workspace_id (vazio quando ausente)", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ id: "abc", created_at: "", updated_at: "", title: "" }),
    );
    await createThread();
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(opts.body as string)).toEqual({ workspace_id: "" });
  });

  it("updateThread: envia só title quando só title é passado (pinned ausente do body)", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ id: "t1", created_at: "", updated_at: "", title: "Novo" }),
    );
    await updateThread("t1", { title: "Novo" });
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(opts.body as string);
    expect(body).toEqual({ thread_id: "t1", title: "Novo" });
    expect(body).not.toHaveProperty("pinned");
  });

  it("updateThread: envia só pinned quando só pinned é passado (title ausente do body, não reseta título)", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        id: "t1",
        created_at: "",
        updated_at: "",
        title: "Preservado",
        pinned: true,
      }),
    );
    await updateThread("t1", { pinned: true });
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(opts.body as string);
    expect(body).toEqual({ thread_id: "t1", pinned: true });
    expect(body).not.toHaveProperty("title");
  });

  it("updateThread: envia os dois campos quando ambos são passados", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        id: "t1",
        created_at: "",
        updated_at: "",
        title: "Novo",
        pinned: false,
      }),
    );
    await updateThread("t1", { title: "Novo", pinned: false });
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(opts.body as string);
    expect(body).toEqual({ thread_id: "t1", title: "Novo", pinned: false });
  });
});

describe("auth no postRpc", () => {
  it("401 dispara /auth/refresh e retenta uma única vez", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({}, 401)) // RPC → 401
      .mockResolvedValueOnce(jsonResponse({}, 200)) // /auth/refresh → ok
      .mockResolvedValueOnce(jsonResponse({ messages: [] })); // retry → ok

    const r = await getHistory("t1");
    expect(r.messages).toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(String(fetchMock.mock.calls[1][0])).toContain("/auth/refresh");
  });

  it("erro não-401 lança com o status", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "boom" }, 500));
    await expect(listThreads()).rejects.toThrow(/500/);
  });
});

describe("ThreadService pins", () => {
  it("getThreadPins: POST GetThreadPins com thread_id", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ thread_id: "t1", pins: ["a.py", "b.py"] }),
    );
    const r = await getThreadPins("t1");
    expect(r.pins).toEqual(["a.py", "b.py"]);
    expect(r.thread_id).toBe("t1");

    const [url, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain(
      "/vectora.chat.v1.ThreadService/GetThreadPins",
    );
    expect(opts.method).toBe("POST");
    expect(opts.credentials).toBe("include");
    expect(JSON.parse(opts.body as string)).toEqual({ thread_id: "t1" });
  });

  it("getThreadPins: lista vazia quando a sessão não tem pins", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ thread_id: "t1", pins: [] }),
    );
    const r = await getThreadPins("t1");
    expect(r.pins).toEqual([]);
  });

  it("setThreadPins: POST SetThreadPins com thread_id e pins", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ thread_id: "t2", pins: ["x.py"] }),
    );
    const r = await setThreadPins("t2", ["x.py"]);
    expect(r.pins).toEqual(["x.py"]);

    const [url, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain(
      "/vectora.chat.v1.ThreadService/SetThreadPins",
    );
    expect(JSON.parse(opts.body as string)).toEqual({
      thread_id: "t2",
      pins: ["x.py"],
    });
  });

  it("setThreadPins: envia lista vazia (limpar pins)", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ thread_id: "t3", pins: [] }),
    );
    const r = await setThreadPins("t3", []);
    expect(r.pins).toEqual([]);
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(opts.body as string)).toEqual({
      thread_id: "t3",
      pins: [],
    });
  });

  it("setThreadPins: devolve a lista normalizada do backend (dedup)", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ thread_id: "t4", pins: ["a.py"] }),
    );
    const r = await setThreadPins("t4", ["a.py", "a.py"]);
    expect(r.pins).toEqual(["a.py"]);
  });

  it("setThreadPins: preserva ordem de múltiplos pins", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ thread_id: "t5", pins: ["a.py", "b.py", "c.py"] }),
    );
    const r = await setThreadPins("t5", ["a.py", "b.py", "c.py"]);
    expect(r.pins).toEqual(["a.py", "b.py", "c.py"]);
  });

  it("getThreadPins: 401 dispara refresh e retenta", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({}, 401))
      .mockResolvedValueOnce(jsonResponse({}, 200))
      .mockResolvedValueOnce(jsonResponse({ thread_id: "t6", pins: ["z.py"] }));
    const r = await getThreadPins("t6");
    expect(r.pins).toEqual(["z.py"]);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("setThreadPins: erro não-401 lança com o status", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "boom" }, 500));
    await expect(setThreadPins("t7", ["a.py"])).rejects.toThrow(/500/);
  });

  it("getThreadPins: erro não-401 lança com o status", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "x" }, 503));
    await expect(getThreadPins("t8")).rejects.toThrow(/503/);
  });
});

describe("getHistory — guarda de thread_id vazio", () => {
  it("erro/borda: id vazio não vai à rede e devolve histórico vazio", async () => {
    // Regressão: pedir histórico antes de existir um id produzia
    // `404: Thread '' not found` com traceback no backend em todo boot.
    const r = await getHistory("");

    expect(fetchMock).not.toHaveBeenCalled();
    expect(r.messages).toEqual([]);
  });

  it("id só com espaços também é recusado (não vira request)", async () => {
    await expect(getHistory("   ")).resolves.toMatchObject({ messages: [] });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("id válido continua indo à rede normalmente", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ messages: [{ id: "m1" }] }));
    const r = await getHistory("t1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(r.messages).toHaveLength(1);
  });
});

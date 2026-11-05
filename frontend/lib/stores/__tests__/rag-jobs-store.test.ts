/**
 * Tests para o `rag-jobs-store`: parte determinística (dismiss, falha de
 * start). O poll por setInterval não é exercido aqui.
 */

import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { useRagJobsStore, type RagJob } from "../rag-jobs-store";

beforeEach(() => {
  useRagJobsStore.setState({ jobs: {} });
});

afterEach(() => {
  vi.restoreAllMocks();
});

function job(jobId: string, over: Partial<RagJob> = {}): RagJob {
  return {
    jobId,
    workspaceId: "ws1",
    path: "/x",
    total: 10,
    processed: 0,
    failed: 0,
    status: "indexing",
    ...over,
  };
}

function pollFetch(pollBody: object) {
  return vi.fn(async (url: string) => {
    if (String(url).includes("/rag/ingest")) {
      return {
        ok: true,
        json: async () => ({
          job_id: "jp",
          total_chunks: 5,
          status: "indexing",
        }),
      } as Response;
    }
    return { ok: true, json: async () => pollBody } as Response;
  });
}

describe("rag-jobs-store", () => {
  it("dismiss remove o job da lista", () => {
    useRagJobsStore.setState({ jobs: { j1: job("j1") } });
    useRagJobsStore.getState().dismiss("j1");
    expect("j1" in useRagJobsStore.getState().jobs).toBe(false);
  });

  it("dismiss de job inexistente não quebra", () => {
    expect(() => useRagJobsStore.getState().dismiss("nope")).not.toThrow();
  });

  it("applyEvent atualiza progresso de um job existente (evento SSE)", () => {
    useRagJobsStore.setState({ jobs: { j1: job("j1") } });
    useRagJobsStore.getState().applyEvent({
      job_id: "j1",
      total: 10,
      processed: 4,
      failed: 0,
      status: "indexing",
    });
    const updated = useRagJobsStore.getState().jobs.j1;
    expect(updated.processed).toBe(4);
    expect(updated.status).toBe("indexing");
  });

  it("applyEvent para job desconhecido não cria entrada nova", () => {
    useRagJobsStore.getState().applyEvent({
      job_id: "fantasma",
      total: 1,
      processed: 1,
      failed: 0,
      status: "done",
    });
    expect("fantasma" in useRagJobsStore.getState().jobs).toBe(false);
  });

  it("applyEvent com status terminal guarda o motivo de erro", () => {
    useRagJobsStore.setState({ jobs: { j1: job("j1") } });
    useRagJobsStore.getState().applyEvent({
      job_id: "j1",
      total: 10,
      processed: 3,
      failed: 7,
      status: "failed",
      error_reason: "Cohere 429",
    });
    const updated = useRagJobsStore.getState().jobs.j1;
    expect(updated.status).toBe("failed");
    expect(updated.errorReason).toBe("Cohere 429");
  });

  it("guarda o motivo quando o job está pausado (rate limit)", () => {
    useRagJobsStore.setState({
      jobs: {
        j1: job("j1", { status: "paused", errorReason: "Cohere 429" }),
      },
    });
    expect(useRagJobsStore.getState().jobs.j1.status).toBe("paused");
    expect(useRagJobsStore.getState().jobs.j1.errorReason).toBe("Cohere 429");
  });

  it("start devolve null quando o POST falha", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false }) as Response),
    );
    const result = await useRagJobsStore
      .getState()
      .start("ws1", "/pasta", "all");
    expect(result).toBeNull();
    expect(Object.keys(useRagJobsStore.getState().jobs)).toHaveLength(0);
  });

  it("start com no_files cria o job em estado no_files e não pollla", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          ({
            ok: true,
            json: async () => ({
              job_id: "jnf",
              total_chunks: 0,
              status: "no_files",
            }),
          }) as Response,
      ),
    );
    const id = await useRagJobsStore.getState().start("ws1", "/vazia", "all");
    expect(id).toBe("jnf");
    const tracked = useRagJobsStore.getState().jobs.jnf;
    expect(tracked.status).toBe("no_files");
    expect(tracked.total).toBe(0);
  });

  it("start indexando registra o job; dismiss para o poll e remove", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).includes("/rag/ingest")) {
          return {
            ok: true,
            json: async () => ({
              job_id: "jix",
              total_chunks: 5,
              status: "indexing",
            }),
          } as Response;
        }
        return {
          ok: true,
          json: async () => ({
            total: 5,
            processed: 0,
            failed: 0,
            status: "indexing",
          }),
        } as Response;
      }),
    );
    const id = await useRagJobsStore.getState().start("ws1", "/docs", "code");
    expect(id).toBe("jix");
    expect(useRagJobsStore.getState().jobs.jix?.status).toBe("indexing");

    useRagJobsStore.getState().dismiss("jix");
    expect("jix" in useRagJobsStore.getState().jobs).toBe(false);
    vi.useRealTimers();
  });

  it("start devolve null quando o fetch lança", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network");
      }),
    );
    const r = await useRagJobsStore.getState().start("ws1", "/x", "all");
    expect(r).toBeNull();
    expect(Object.keys(useRagJobsStore.getState().jobs)).toHaveLength(0);
  });

  it("start envia path e file_types no body do POST", async () => {
    const fetchMock = vi.fn((..._a: unknown[]) =>
      Promise.resolve({
        ok: true,
        json: async () => ({
          job_id: "j",
          total_chunks: 3,
          status: "no_files",
        }),
      } as Response),
    );
    vi.stubGlobal("fetch", fetchMock);
    await useRagJobsStore.getState().start("ws1", "/p", "markdown");
    const body = JSON.parse(
      (fetchMock.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body.path).toBe("/p");
    expect(body.file_types).toBe("markdown");
  });

  it("start define total a partir de total_chunks", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          ({
            ok: true,
            json: async () => ({
              job_id: "jt",
              total_chunks: 7,
              status: "no_files",
            }),
          }) as Response,
      ),
    );
    await useRagJobsStore.getState().start("ws1", "/p", "all");
    expect(useRagJobsStore.getState().jobs.jt.total).toBe(7);
  });

  it("poll atualiza processed/failed/status", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      pollFetch({ total: 5, processed: 2, failed: 1, status: "indexing" }),
    );
    await useRagJobsStore.getState().start("ws1", "/d", "code");
    await vi.advanceTimersByTimeAsync(1300);
    const j = useRagJobsStore.getState().jobs.jp;
    expect(j.processed).toBe(2);
    expect(j.failed).toBe(1);
    useRagJobsStore.getState().dismiss("jp");
    vi.useRealTimers();
  });

  it("poll done para o timer", async () => {
    vi.useFakeTimers();
    const fetchMock = pollFetch({
      total: 5,
      processed: 5,
      failed: 0,
      status: "done",
    });
    vi.stubGlobal("fetch", fetchMock);
    await useRagJobsStore.getState().start("ws1", "/d", "code");
    await vi.advanceTimersByTimeAsync(1300);
    const callsAfter = fetchMock.mock.calls.length;
    await vi.advanceTimersByTimeAsync(4000);
    expect(fetchMock.mock.calls.length).toBe(callsAfter);
    expect(useRagJobsStore.getState().jobs.jp.status).toBe("done");
    vi.useRealTimers();
  });

  it("poll paused para o timer e guarda o motivo", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      pollFetch({
        total: 5,
        processed: 3,
        failed: 0,
        status: "paused",
        error_reason: "Cohere 429",
      }),
    );
    await useRagJobsStore.getState().start("ws1", "/d", "code");
    await vi.advanceTimersByTimeAsync(1300);
    const j = useRagJobsStore.getState().jobs.jp;
    expect(j.status).toBe("paused");
    expect(j.errorReason).toBe("Cohere 429");
    vi.useRealTimers();
  });

  it("poll failed para o timer", async () => {
    vi.useFakeTimers();
    const fetchMock = pollFetch({
      total: 5,
      processed: 1,
      failed: 4,
      status: "failed",
    });
    vi.stubGlobal("fetch", fetchMock);
    await useRagJobsStore.getState().start("ws1", "/d", "code");
    await vi.advanceTimersByTimeAsync(1300);
    const callsAfter = fetchMock.mock.calls.length;
    await vi.advanceTimersByTimeAsync(4000);
    expect(fetchMock.mock.calls.length).toBe(callsAfter);
    vi.useRealTimers();
  });

  it("poll com resposta não-ok mantém o job inalterado", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).includes("/rag/ingest")) {
          return {
            ok: true,
            json: async () => ({
              job_id: "jp",
              total_chunks: 5,
              status: "indexing",
            }),
          } as Response;
        }
        return { ok: false } as Response;
      }),
    );
    await useRagJobsStore.getState().start("ws1", "/d", "code");
    await vi.advanceTimersByTimeAsync(1300);
    expect(useRagJobsStore.getState().jobs.jp.status).toBe("indexing");
    useRagJobsStore.getState().dismiss("jp");
    vi.useRealTimers();
  });

  it("dismiss antes do poll não recria o job", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      pollFetch({ total: 5, processed: 5, failed: 0, status: "indexing" }),
    );
    await useRagJobsStore.getState().start("ws1", "/d", "code");
    useRagJobsStore.getState().dismiss("jp");
    await vi.advanceTimersByTimeAsync(4000);
    expect("jp" in useRagJobsStore.getState().jobs).toBe(false);
    vi.useRealTimers();
  });
});

// @vitest-environment jsdom
/**
 * useContextGraph — busca de status/report, build, polling e getHtmlUrl.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

// ── fetch global mockado ─────────────────────────────────────────────────────

const FETCH_MOCK = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", FETCH_MOCK);
  FETCH_MOCK.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function mockOk(body: object): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFail(status = 404): Response {
  return new Response(JSON.stringify({ detail: "not found" }), { status });
}

import { useContextGraph } from "@/lib/hooks/use-context-graph";

// ── testes ───────────────────────────────────────────────────────────────────

describe("useContextGraph", () => {
  it("busca status ao montar com workspaceId válido", async () => {
    FETCH_MOCK.mockResolvedValueOnce(mockOk({ status: "not_built" }));

    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});

    expect(FETCH_MOCK).toHaveBeenCalledWith(
      expect.stringContaining("/workspaces/ws1/context-graph/status"),
    );
    expect(result.current.status.status).toBe("not_built");
  });

  it("não busca e mantém unknown quando workspaceId é null", () => {
    const { result } = renderHook(() => useContextGraph(null));
    expect(FETCH_MOCK).not.toHaveBeenCalled();
    expect(result.current.status.status).toBe("unknown");
  });

  it("não busca quando workspaceId é string vazia", () => {
    const { result } = renderHook(() => useContextGraph(""));
    expect(FETCH_MOCK).not.toHaveBeenCalled();
    expect(result.current.status.status).toBe("unknown");
  });

  it("busca report automaticamente quando status é done", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      mockOk({ status: "done", node_count: 5, edge_count: 3 }),
    ).mockResolvedValueOnce(mockOk({ report: "**God nodes**\n- Auth\n" }));

    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});

    expect(FETCH_MOCK).toHaveBeenCalledTimes(2);
    expect(result.current.report).toContain("God nodes");
    expect(result.current.status.node_count).toBe(5);
  });

  it("mantém report null quando status não é done", async () => {
    FETCH_MOCK.mockResolvedValueOnce(mockOk({ status: "running" }));

    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});

    expect(result.current.report).toBeNull();
  });

  it("build POST e seta status running após resposta ok", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      mockOk({ status: "not_built" }),
    ).mockResolvedValueOnce(
      mockOk({ status: "queued", message: "enfileirado" }),
    );

    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});

    await act(async () => {
      await result.current.build();
    });

    expect(FETCH_MOCK).toHaveBeenCalledWith(
      expect.stringContaining("/build"),
      expect.objectContaining({ method: "POST" }),
    );
    expect(result.current.status.status).toBe("running");
  });

  it("build envia opções model/mode/update no body", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      mockOk({ status: "not_built" }),
    ).mockResolvedValueOnce(mockOk({ status: "queued" }));

    const { result } = renderHook(() => useContextGraph("ws2"));
    await act(async () => {});

    await act(async () => {
      await result.current.build({
        model: "gpt-4o",
        mode: "ast",
        update: true,
      });
    });

    const call = FETCH_MOCK.mock.calls.find((args) =>
      String(args[0]).includes("/build"),
    );
    const body = JSON.parse(call![1].body as string);
    expect(body.model).toBe("gpt-4o");
    expect(body.mode).toBe("ast");
    expect(body.update).toBe(true);
  });

  it("resume envia resume=true no body do build (retoma pausado)", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      mockOk({ status: "paused" }),
    ).mockResolvedValueOnce(mockOk({ status: "queued" }));

    const { result } = renderHook(() => useContextGraph("ws-resume"));
    await act(async () => {});

    await act(async () => {
      await result.current.resume();
    });

    const call = FETCH_MOCK.mock.calls.find(
      (args) =>
        String(args[0]).includes("/build") && args[1]?.method === "POST",
    );
    const body = JSON.parse(call![1].body as string);
    expect(body.resume).toBe(true);
    expect(body.update).toBe(false);
  });

  it("build falha silenciosamente se fetch rejeita (sem lançar)", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      mockOk({ status: "not_built" }),
    ).mockRejectedValueOnce(new Error("Network error"));

    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});

    await act(async () => {
      await result.current.build();
    });

    expect(result.current.loading).toBe(false);
  });

  it("getHtmlUrl retorna URL com workspaceId codificado", async () => {
    FETCH_MOCK.mockResolvedValue(mockOk({ status: "unknown" }));
    const { result } = renderHook(() => useContextGraph("ws-abc"));
    await act(async () => {});
    expect(result.current.getHtmlUrl()).toContain(
      "/workspaces/ws-abc/context-graph/html",
    );
  });

  it("getHtmlUrl retorna null sem workspaceId", () => {
    const { result } = renderHook(() => useContextGraph(null));
    expect(result.current.getHtmlUrl()).toBeNull();
  });

  it("fetch com erro de rede não quebra o hook", async () => {
    FETCH_MOCK.mockRejectedValueOnce(new Error("offline"));

    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});

    expect(result.current.status.status).toBe("unknown");
  });

  it("resposta não-ok do status não altera o estado além do fallback", async () => {
    FETCH_MOCK.mockResolvedValueOnce(mockFail(500));

    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});

    expect(result.current.status.status).toBe("unknown");
  });

  it("status JSON malformado cai no fallback unknown", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      new Response("NOTJSON{", {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});
    expect(result.current.status.status).toBe("unknown");
  });

  it("status queued é preservado", async () => {
    FETCH_MOCK.mockResolvedValueOnce(mockOk({ status: "queued" }));
    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});
    expect(result.current.status.status).toBe("queued");
  });

  it("report não é setado quando o backend devolve report vazio", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      mockOk({ status: "done" }),
    ).mockResolvedValueOnce(mockOk({ report: "" }));
    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});
    expect(result.current.report).toBeNull();
  });

  it("report não-ok mantém report null", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      mockOk({ status: "done" }),
    ).mockResolvedValueOnce(mockFail(500));
    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});
    expect(result.current.report).toBeNull();
  });

  it("getHtmlUrl codifica caracteres especiais do workspaceId", async () => {
    FETCH_MOCK.mockResolvedValue(mockOk({ status: "unknown" }));
    const { result } = renderHook(() => useContextGraph("a b/c"));
    await act(async () => {});
    const url = result.current.getHtmlUrl();
    expect(url).toContain("a%20b%2Fc");
  });

  // ── queryAffected ──────────────────────────────────────────────────────────

  it("queryAffected: POST /affected com node_query e depth no body", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      mockOk({ status: "not_built" }),
    ).mockResolvedValueOnce(mockOk({ answer: "impacto: X" }));
    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});
    let out = "";
    await act(async () => {
      out = await result.current.queryAffected("AuthService", 3);
    });
    const call = FETCH_MOCK.mock.calls.find((a) =>
      String(a[0]).includes("/affected"),
    );
    const body = JSON.parse(call![1].body as string);
    expect(body.node_query).toBe("AuthService");
    expect(body.depth).toBe(3);
    expect(out).toBe("impacto: X");
  });

  it("queryAffected: depth padrão é 2", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      mockOk({ status: "not_built" }),
    ).mockResolvedValueOnce(mockOk({ answer: "ok" }));
    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});
    await act(async () => {
      await result.current.queryAffected("X");
    });
    const call = FETCH_MOCK.mock.calls.find((a) =>
      String(a[0]).includes("/affected"),
    );
    expect(JSON.parse(call![1].body as string).depth).toBe(2);
  });

  it("queryAffected: resposta não-ok retorna string vazia", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      mockOk({ status: "not_built" }),
    ).mockResolvedValueOnce(mockFail(404));
    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});
    let out = "x";
    await act(async () => {
      out = await result.current.queryAffected("X");
    });
    expect(out).toBe("");
  });

  it("queryAffected: sem workspaceId retorna string vazia sem fetch", async () => {
    const { result } = renderHook(() => useContextGraph(null));
    const out = await result.current.queryAffected("X");
    expect(out).toBe("");
    expect(FETCH_MOCK).not.toHaveBeenCalled();
  });

  it("queryAffected: fetch rejeitado retorna string vazia", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      mockOk({ status: "not_built" }),
    ).mockRejectedValueOnce(new Error("offline"));
    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});
    let out = "x";
    await act(async () => {
      out = await result.current.queryAffected("X");
    });
    expect(out).toBe("");
  });

  it("queryAffected: answer ausente retorna string vazia", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      mockOk({ status: "not_built" }),
    ).mockResolvedValueOnce(mockOk({ nada: 1 }));
    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});
    let out = "x";
    await act(async () => {
      out = await result.current.queryAffected("X");
    });
    expect(out).toBe("");
  });

  // ── update (incremental) ─────────────────────────────────────────────────────

  it("update chama build com update:true", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      mockOk({ status: "not_built" }),
    ).mockResolvedValueOnce(mockOk({ status: "queued" }));
    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});
    await act(async () => {
      await result.current.update();
    });
    const call = FETCH_MOCK.mock.calls.find((a) =>
      String(a[0]).includes("/build"),
    );
    expect(JSON.parse(call![1].body as string).update).toBe(true);
  });

  it("update propaga o model para o build", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      mockOk({ status: "not_built" }),
    ).mockResolvedValueOnce(mockOk({ status: "queued" }));
    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});
    await act(async () => {
      await result.current.update({ model: "claude" });
    });
    const call = FETCH_MOCK.mock.calls.find((a) =>
      String(a[0]).includes("/build"),
    );
    expect(JSON.parse(call![1].body as string).model).toBe("claude");
  });

  // ── polling ──────────────────────────────────────────────────────────────────

  it("status running agenda polling a cada 3s", async () => {
    vi.useFakeTimers();
    try {
      FETCH_MOCK.mockResolvedValue(mockOk({ status: "running" }));
      renderHook(() => useContextGraph("ws1"));
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      const before = FETCH_MOCK.mock.calls.length;
      await act(async () => {
        vi.advanceTimersByTime(3100);
        await Promise.resolve();
      });
      expect(FETCH_MOCK.mock.calls.length).toBeGreaterThan(before);
    } finally {
      vi.useRealTimers();
    }
  });

  it("status not_built não agenda polling", async () => {
    vi.useFakeTimers();
    try {
      FETCH_MOCK.mockResolvedValue(mockOk({ status: "not_built" }));
      renderHook(() => useContextGraph("ws1"));
      await act(async () => {
        await Promise.resolve();
      });
      const before = FETCH_MOCK.mock.calls.length;
      await act(async () => {
        vi.advanceTimersByTime(6000);
        await Promise.resolve();
      });
      expect(FETCH_MOCK.mock.calls.length).toBe(before);
    } finally {
      vi.useRealTimers();
    }
  });

  // ── status paused (Parte C — quota esgotada) ──────────────────────────────────

  it("status paused é preservado com a mensagem de erro", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      mockOk({ status: "paused", error: "quota esgotada" }),
    );
    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});
    expect(result.current.status.status).toBe("paused");
    expect(result.current.status.error).toBe("quota esgotada");
  });

  it("status paused não dispara busca de report (só done busca)", async () => {
    FETCH_MOCK.mockResolvedValueOnce(mockOk({ status: "paused" }));
    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});
    expect(FETCH_MOCK).toHaveBeenCalledTimes(1);
    expect(result.current.report).toBeNull();
  });

  it("status paused não agenda polling", async () => {
    vi.useFakeTimers();
    try {
      FETCH_MOCK.mockResolvedValue(mockOk({ status: "paused" }));
      renderHook(() => useContextGraph("ws1"));
      await act(async () => {
        await Promise.resolve();
      });
      const before = FETCH_MOCK.mock.calls.length;
      await act(async () => {
        vi.advanceTimersByTime(6000);
        await Promise.resolve();
      });
      expect(FETCH_MOCK.mock.calls.length).toBe(before);
    } finally {
      vi.useRealTimers();
    }
  });

  it("status error é preservado com a mensagem", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      mockOk({ status: "error", error: "boom" }),
    );
    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});
    expect(result.current.status.status).toBe("error");
    expect(result.current.status.error).toBe("boom");
  });

  it("rebuild a partir de paused chama /build (Continuar)", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      mockOk({ status: "paused" }),
    ).mockResolvedValueOnce(mockOk({ status: "queued" }));
    const { result } = renderHook(() => useContextGraph("ws1"));
    await act(async () => {});
    await act(async () => {
      await result.current.build();
    });
    expect(
      FETCH_MOCK.mock.calls.some((a) => String(a[0]).includes("/build")),
    ).toBe(true);
    expect(result.current.status.status).toBe("running");
  });
});

// @vitest-environment jsdom
/**
 * Testes do hook use-webhook-events.
 *
 * Cobre:
 * - onEvent chamado quando SSE recebe mensagem
 * - Eventos de sistema (type="connected") são ignorados
 * - Múltiplos handlers registrados via onWebhookEvent recebem o mesmo evento
 * - Cleanup remove o handler ao desmontar
 * - Payload JSON inválido não quebra o hook
 */

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { renderHook, cleanup, act } from "@testing-library/react";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Mock do EventSource
// ---------------------------------------------------------------------------

class MockEventSource {
  url: string;
  listeners: Record<string, ((ev: { data: string }) => void)[]> = {};
  static instances: MockEventSource[] = [];

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, handler: (ev: { data: string }) => void) {
    (this.listeners[type] ??= []).push(handler);
  }

  close() {}

  dispatchMessage(data: string) {
    for (const h of this.listeners.message ?? []) h({ data });
  }

  triggerError() {
    for (const h of this.listeners.error ?? []) h({ data: "" });
  }
}

beforeEach(() => {
  MockEventSource.instances = [];
  vi.stubGlobal("EventSource", MockEventSource);
});

// ---------------------------------------------------------------------------
// Testes
// ---------------------------------------------------------------------------

describe("useWebhookEvents", () => {
  it("chama onEvent com dados do evento SSE recebido", async () => {
    const { useWebhookEvents } = await import("../use-webhook-events");
    const received: object[] = [];

    renderHook(() =>
      useWebhookEvents((event) => {
        received.push(event);
      }),
    );

    await act(async () => {
      MockEventSource.instances[0]?.dispatchMessage(
        JSON.stringify({
          type: "webhook_event",
          provider: "github",
          event_type: "push",
          data: { ref: "main" },
        }),
      );
    });

    expect(received).toHaveLength(1);
    expect((received[0] as { provider: string }).provider).toBe("github");
    expect((received[0] as { event_type: string }).event_type).toBe("push");
  });

  it("ignora eventos do sistema (type=connected)", async () => {
    const { useWebhookEvents } = await import("../use-webhook-events");
    const received: object[] = [];

    renderHook(() =>
      useWebhookEvents((event) => {
        received.push(event);
      }),
    );

    await act(async () => {
      MockEventSource.instances[0]?.dispatchMessage(
        JSON.stringify({ type: "connected", provider: "system" }),
      );
    });

    expect(received).toHaveLength(0);
  });

  it("payload JSON inválido não levanta exceção", async () => {
    const { useWebhookEvents } = await import("../use-webhook-events");
    const received: object[] = [];

    renderHook(() =>
      useWebhookEvents((event) => {
        received.push(event);
      }),
    );

    await act(async () => {
      MockEventSource.instances[0]?.dispatchMessage("nao-e-json{{{");
    });

    expect(received).toHaveLength(0);
  });

  it("múltiplos handlers via onWebhookEvent recebem o mesmo evento", async () => {
    const { useWebhookEvents, onWebhookEvent } =
      await import("../use-webhook-events");
    const a: string[] = [];
    const b: string[] = [];

    const removeA = onWebhookEvent((e) => a.push(e.event_type));
    const removeB = onWebhookEvent((e) => b.push(e.event_type));

    renderHook(() => useWebhookEvents());

    await act(async () => {
      MockEventSource.instances[0]?.dispatchMessage(
        JSON.stringify({
          type: "webhook_event",
          provider: "slack",
          event_type: "message",
          data: {},
        }),
      );
    });

    expect(a).toContain("message");
    expect(b).toContain("message");

    removeA();
    removeB();
  });

  it("remove handler ao desmontar", async () => {
    const { useWebhookEvents } = await import("../use-webhook-events");
    const received: object[] = [];

    const { unmount } = renderHook(() =>
      useWebhookEvents((event) => {
        received.push(event);
      }),
    );

    unmount();

    await act(async () => {
      MockEventSource.instances[0]?.dispatchMessage(
        JSON.stringify({
          type: "webhook_event",
          provider: "github",
          event_type: "push",
          data: {},
        }),
      );
    });

    // Handler foi removido ao desmontar — não deve receber nada novo
    expect(received).toHaveLength(0);
  });
});

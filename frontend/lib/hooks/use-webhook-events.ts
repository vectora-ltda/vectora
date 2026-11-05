"use client";

/**
 * use-webhook-events — conecta ao SSE GET /webhook/events e despacha eventos
 * recebidos (GitHub CI, PRs, Slack, etc.) para os stores relevantes.
 *
 * - Reconecta automaticamente com backoff exponencial (1s → 30s)
 * - Para ao desmontar
 * - Repassa eventos de CI/PR para o workbench-store via toast (quando disponível)
 */

import { useEffect, useRef } from "react";

interface WebhookEvent {
  type: string;
  provider: string;
  event_type: string;
  data: Record<string, unknown>;
}

const MIN_RECONNECT_MS = 1_000;
const MAX_RECONNECT_MS = 30_000;

type WebhookEventHandler = (event: WebhookEvent) => void;

const handlers: Set<WebhookEventHandler> = new Set();

export function onWebhookEvent(handler: WebhookEventHandler): () => void {
  handlers.add(handler);
  return () => handlers.delete(handler);
}

let globalEs: EventSource | null = null;
let globalReconnectTimeout: ReturnType<typeof setTimeout> | null = null;
let globalDelay = MIN_RECONNECT_MS;
let refCount = 0;

function startGlobalSSE(): void {
  if (globalEs) return;

  function connect() {
    const es = new EventSource("/webhook/events");
    globalEs = es;

    es.addEventListener("message", (ev: MessageEvent) => {
      try {
        const event = JSON.parse(ev.data as string) as WebhookEvent;
        if (event.type === "connected") return;
        globalDelay = MIN_RECONNECT_MS;
        for (const handler of handlers) {
          handler(event);
        }
      } catch {
        // payload malformado — ignora
      }
    });

    es.addEventListener("error", () => {
      es.close();
      globalEs = null;
      globalReconnectTimeout = setTimeout(() => {
        globalDelay = Math.min(globalDelay * 2, MAX_RECONNECT_MS);
        connect();
      }, globalDelay);
    });
  }

  connect();
}

function stopGlobalSSE(): void {
  if (globalReconnectTimeout) {
    clearTimeout(globalReconnectTimeout);
    globalReconnectTimeout = null;
  }
  globalEs?.close();
  globalEs = null;
  globalDelay = MIN_RECONNECT_MS;
}

export function useWebhookEvents(onEvent?: WebhookEventHandler): void {
  const handlerRef = useRef(onEvent);

  useEffect(() => {
    handlerRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    refCount++;
    startGlobalSSE();

    // Sempre registra o wrapper; ele lê handlerRef.current (atualizado a cada
    // render) e é no-op quando não há onEvent — assim o efeito não depende de
    // `onEvent` e monta só uma vez.
    const wrapped: WebhookEventHandler = (event) => handlerRef.current?.(event);
    handlers.add(wrapped);

    return () => {
      handlers.delete(wrapped);
      refCount--;
      if (refCount <= 0) {
        refCount = 0;
        stopGlobalSSE();
      }
    };
  }, []);
}

/** Browser-side sandbox boundary for frontend VEXT contributions. */

import type {
  VextContext,
  VextManifest,
  VextRequest,
  VextResponse,
} from "./index.js";

export interface SandboxedExtension {
  readonly frame: HTMLIFrameElement;
  readonly context: VextContext;
  dispose(): void;
}

/**
 * Mount a frontend bundle without granting it Vectora's privileged DOM or
 * origin. Requests leave through postMessage and are answered by the host.
 */
export function mountSandboxedExtension(
  container: HTMLElement,
  entrypoint: string,
  manifest: VextManifest,
  onRequest: (
    method: string,
    params: Record<string, unknown>,
  ) => Promise<unknown>,
): SandboxedExtension {
  const frame = document.createElement("iframe");
  const pending = new Map<
    number | string,
    { resolve: (value: unknown) => void; reject: (error: Error) => void }
  >();
  const queued = new Map<number | string, VextRequest>();
  let nextId = 1;
  let loaded = false;
  frame.setAttribute("sandbox", "allow-scripts");
  frame.referrerPolicy = "no-referrer";
  frame.setAttribute("title", manifest.name);
  frame.src = entrypoint;
  const listener = async (
    event: MessageEvent<
      VextResponse & { method?: string; params?: Record<string, unknown> }
    >,
  ) => {
    if (event.source !== frame.contentWindow || event.data?.jsonrpc !== "2.0")
      return;
    const request = event.data;
    if (typeof request.id !== "number" && typeof request.id !== "string")
      return;
    if (typeof request.method !== "string") {
      const waiter = pending.get(request.id);
      if (!waiter) return;
      pending.delete(request.id);
      if (typeof request.error === "string")
        waiter.reject(new Error(request.error));
      else waiter.resolve(request.result);
      return;
    }
    if (!request.params || typeof request.params !== "object") return;
    try {
      const result = await onRequest(request.method, request.params);
      frame.contentWindow?.postMessage(
        { jsonrpc: "2.0", id: request.id, result } satisfies VextResponse,
        "*",
      );
    } catch (error) {
      frame.contentWindow?.postMessage(
        {
          jsonrpc: "2.0",
          id: request.id,
          error: error instanceof Error ? error.message : String(error),
        } satisfies VextResponse,
        "*",
      );
    }
  };
  window.addEventListener("message", listener);
  const onLoad = () => {
    loaded = true;
    frame.contentWindow?.postMessage(
      { type: "vectora:vext:init", manifest },
      "*",
    );
    for (const message of queued.values())
      frame.contentWindow?.postMessage(message, "*");
    queued.clear();
  };
  const onError = () => {
    const error = new Error("sandbox failed to load");
    for (const waiter of pending.values()) waiter.reject(error);
    queued.clear();
    pending.clear();
  };
  frame.addEventListener("load", onLoad, { once: true });
  frame.addEventListener("error", onError, { once: true });
  container.append(frame);
  return {
    frame,
    context: {
      manifest,
      request: <T = unknown>(method: string, params = {}) => {
        const id = nextId++;
        return new Promise<T>((resolve, reject) => {
          pending.set(id, { resolve: (value) => resolve(value as T), reject });
          const message: VextRequest = { jsonrpc: "2.0", id, method, params };
          if (loaded) frame.contentWindow?.postMessage(message, "*");
          else queued.set(id, message);
        });
      },
    },
    dispose: () => {
      window.removeEventListener("message", listener);
      frame.removeEventListener("load", onLoad);
      frame.removeEventListener("error", onError);
      loaded = false;
      queued.clear();
      for (const waiter of pending.values())
        waiter.reject(new Error("sandbox disposed"));
      pending.clear();
      frame.remove();
    },
  };
}

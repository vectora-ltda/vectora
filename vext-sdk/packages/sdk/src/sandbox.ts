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
  let hasLoaded = false;
  const expectedOrigin = (() => {
    try {
      return new URL(entrypoint, window.location.href).origin;
    } catch {
      return window.location.origin;
    }
  })();
  let channel: MessageChannel | undefined;
  let channelPort: MessagePort | undefined;
  frame.setAttribute("sandbox", "allow-scripts");
  const networkAllowed = manifest.permissions.includes("network");
  frame.setAttribute(
    "csp",
    `default-src 'self'; script-src 'unsafe-inline' 'unsafe-eval'; connect-src ${networkAllowed ? "*" : "'none'"}`,
  );
  frame.referrerPolicy = "no-referrer";
  frame.setAttribute("title", manifest.name);
  frame.src = entrypoint;
  const listener = async (
    event: MessageEvent<
      VextResponse & { method?: string; params?: Record<string, unknown> }
    >,
    fromPort = false,
  ) => {
    if (
      (!fromPort &&
        (event.source !== frame.contentWindow ||
          (event.origin !== "" &&
            event.origin !== "null" &&
            event.origin !== expectedOrigin))) ||
      event.data?.jsonrpc !== "2.0"
    )
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
      const response = { jsonrpc: "2.0", id: request.id, result } satisfies VextResponse;
      if (fromPort) channelPort?.postMessage(response);
      else frame.contentWindow?.postMessage(response, expectedOrigin);
    } catch (error) {
      const response = {
          jsonrpc: "2.0",
          id: request.id,
          error: error instanceof Error ? error.message : String(error),
        } satisfies VextResponse;
      if (fromPort) channelPort?.postMessage(response);
      else frame.contentWindow?.postMessage(response, expectedOrigin);
    }
  };
  window.addEventListener("message", listener);
  const onLoad = () => {
    if (hasLoaded) {
      for (const waiter of pending.values())
        waiter.reject(new Error("sandbox document navigated"));
      pending.clear();
      queued.clear();
    }
    channel?.port1.close();
    channel?.port2.close();
    channel = new MessageChannel();
    channelPort = channel.port1;
    channelPort.onmessage = (event) => {
      void listener(event, true);
    };
    channelPort.start();
    loaded = true;
    hasLoaded = true;
    frame.contentWindow?.postMessage(
      { type: "vectora:vext:init", manifest },
      "*",
      [channel.port2],
    );
    for (const message of queued.values()) channelPort.postMessage(message);
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
          if (loaded) channelPort?.postMessage(message);
          else queued.set(id, message);
        });
      },
    },
    dispose: () => {
      window.removeEventListener("message", listener);
      frame.removeEventListener("load", onLoad);
      frame.removeEventListener("error", onError);
      loaded = false;
      channelPort?.close();
      channelPort = undefined;
      channel = undefined;
      queued.clear();
      for (const waiter of pending.values())
        waiter.reject(new Error("sandbox disposed"));
      pending.clear();
      frame.remove();
    },
  };
}

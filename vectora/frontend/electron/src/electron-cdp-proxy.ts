import {
  createServer,
  request,
  type IncomingMessage,
  type Server,
} from "node:http";
import { connect } from "node:net";

const AUTH_HEADER = "authorization";

/** Aguarda o CDP real do Electron e rejeita uma porta ocupada por outro processo. */
export async function waitForElectronCdpTarget(
  targetPort: number,
  attempts = 40,
): Promise<void> {
  let lastError: unknown;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const browser = await new Promise<string>((resolve, reject) => {
        const probe = request(
          {
            host: "127.0.0.1",
            port: targetPort,
            path: "/json/version",
            method: "GET",
            timeout: 500,
          },
          (response) => {
            const chunks: Buffer[] = [];
            response.on("data", (chunk: Buffer) => chunks.push(chunk));
            response.on("end", () => {
              if (response.statusCode !== 200) {
                reject(new Error(`CDP retornou HTTP ${response.statusCode}`));
                return;
              }
              try {
                const payload = JSON.parse(
                  Buffer.concat(chunks).toString("utf8"),
                ) as { Browser?: unknown; "User-Agent"?: unknown };
                // Electron's Chromium reports a Chrome product string in
                // `Browser` on some versions, while its user agent retains
                // the Electron product. Validate both fields so a healthy
                // Electron endpoint is not rejected and the authenticated
                // proxy can start for the backend.
                const browser =
                  typeof payload.Browser === "string" ? payload.Browser : "";
                const userAgent =
                  typeof payload["User-Agent"] === "string"
                    ? payload["User-Agent"]
                    : "";
                if (
                  !`${browser} ${userAgent}`.toLowerCase().includes("electron")
                ) {
                  reject(new Error("a porta CDP não pertence ao Electron"));
                  return;
                }
                resolve(`${browser} ${userAgent}`.trim());
              } catch (error) {
                reject(error);
              }
            });
          },
        );
        probe.once("timeout", () => probe.destroy(new Error("timeout")));
        probe.once("error", reject);
        probe.end();
      });
      if (browser) return;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(
    `CDP do Electron não ficou disponível na porta ${targetPort}: ${String(lastError)}`,
  );
}

function authorized(requestMessage: IncomingMessage, token: string): boolean {
  return requestMessage.headers[AUTH_HEADER] === `Bearer ${token}`;
}

function withoutAuth(
  headers: IncomingMessage["headers"],
): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(headers)) {
    if (key === AUTH_HEADER || value === undefined) continue;
    result[key] = Array.isArray(value) ? value.join(", ") : value;
  }
  return result;
}

/** Exposes Electron's private CDP port only through an authenticated loopback proxy. */
export async function startElectronCdpProxy(
  targetPort: number,
  listenPort: number,
  token: string,
): Promise<Server> {
  const server = createServer((incoming, outgoing) => {
    if (!authorized(incoming, token)) {
      outgoing.writeHead(401, { "content-type": "application/json" });
      outgoing.end('{"error":"unauthorized"}');
      return;
    }
    const upstream = request(
      {
        host: "127.0.0.1",
        port: targetPort,
        path: incoming.url,
        method: incoming.method,
        headers: withoutAuth(incoming.headers),
      },
      (response) => {
        const discovery =
          incoming.url === "/json/version" || incoming.url === "/json/list";
        if (!discovery) {
          outgoing.writeHead(response.statusCode ?? 502, response.headers);
          response.pipe(outgoing);
          return;
        }
        const chunks: Buffer[] = [];
        response.on("data", (chunk: Buffer) => chunks.push(chunk));
        response.on("end", () => {
          try {
            const payload = JSON.parse(
              Buffer.concat(chunks).toString("utf8"),
            ) as Record<string, unknown> | Array<Record<string, unknown>>;
            const rewrite = (entry: Record<string, unknown>): void => {
              if (typeof entry.webSocketDebuggerUrl === "string") {
                entry.webSocketDebuggerUrl = entry.webSocketDebuggerUrl.replace(
                  `127.0.0.1:${targetPort}`,
                  `127.0.0.1:${listenPort}`,
                );
              }
            };
            if (Array.isArray(payload)) payload.forEach(rewrite);
            else rewrite(payload);
            const body = Buffer.from(JSON.stringify(payload));
            outgoing.writeHead(response.statusCode ?? 200, {
              "content-type": "application/json",
              "content-length": body.length,
            });
            outgoing.end(body);
          } catch {
            outgoing.writeHead(502);
            outgoing.end();
          }
        });
      },
    );
    const closeWithBadGateway = (): void => {
      if (!outgoing.headersSent) outgoing.writeHead(502);
      if (!outgoing.writableEnded) outgoing.end();
    };
    upstream.once("error", closeWithBadGateway);
    incoming.once("error", closeWithBadGateway);
    incoming.pipe(upstream);
  });

  server.on("upgrade", (incoming, socket, head) => {
    if (!authorized(incoming, token)) {
      socket.end("HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n");
      return;
    }
    const upstream = connect(targetPort, "127.0.0.1", () => {
      const headers = Object.entries(withoutAuth(incoming.headers))
        .map(([key, value]) => `${key}: ${value}`)
        .join("\r\n");
      upstream.write(
        `${incoming.method} ${incoming.url} HTTP/${incoming.httpVersion}\r\n${headers}\r\n\r\n`,
      );
      if (head.length) upstream.write(head);
      socket.pipe(upstream).pipe(socket);
    });
    const destroyPair = (): void => {
      if (!socket.destroyed) socket.destroy();
      if (!upstream.destroyed) upstream.destroy();
    };
    socket.once("error", destroyPair);
    upstream.once("error", destroyPair);
    socket.once("close", () => {
      if (!upstream.destroyed) upstream.destroy();
    });
    upstream.once("close", () => {
      if (!socket.destroyed) socket.destroy();
    });
  });

  await new Promise<void>((resolve, reject) => {
    server.once("listening", resolve);
    server.once("error", reject);
    server.listen(listenPort, "127.0.0.1");
  });
  return server;
}

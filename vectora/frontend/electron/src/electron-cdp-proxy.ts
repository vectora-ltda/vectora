import {
  createServer,
  request,
  type IncomingMessage,
  type Server,
} from "node:http";
import { connect } from "node:net";

const AUTH_HEADER = "authorization";

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
        outgoing.writeHead(response.statusCode ?? 502, response.headers);
        response.pipe(outgoing);
      },
    );
    upstream.on("error", () => {
      if (!outgoing.headersSent) outgoing.writeHead(502);
      outgoing.end();
    });
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
    upstream.on("error", () => socket.destroy());
  });

  await new Promise<void>((resolve, reject) => {
    server.once("listening", resolve);
    server.once("error", reject);
    server.listen(listenPort, "127.0.0.1");
  });
  return server;
}

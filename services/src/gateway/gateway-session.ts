import type {
  Env,
  QueuedRequest,
  GatewayMessage,
  ClientMessage,
} from "./types";
import { timingSafeEqual, hashConnectorSecret } from "./auth";

const QUEUE_TTL_DEFAULT = 600_000; // 10 min
// 30s é por design pra request/response HTTP síncrono (callback OAuth, etc)
// — não deve subir pra acomodar jobs longos (ex.: revisão de PR completa,
// que pode levar minutos). Job longo usa outro padrão: fire-and-forget
// pelo túnel + callback assíncrono direto do client pra services.vectora.company
// quando terminar, sem manter esta Promise pendente esperando.
const FORWARD_TIMEOUT_MS = 30_000;
const PING_INTERVAL_MS = 20_000;

interface PendingResponse {
  resolve: (msg: ClientMessage & { type: "response" }) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

export class GatewaySession implements DurableObject {
  private ws: WebSocket | null = null;
  private queue: QueuedRequest[] = [];
  private pending = new Map<string, PendingResponse>();
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private readonly ttlMs: number;
  private secretHash: string | null = null;

  constructor(
    private readonly state: DurableObjectState,
    private readonly env: Env,
  ) {
    this.ttlMs = parseInt(env.QUEUE_TTL_MS ?? String(QUEUE_TTL_DEFAULT), 10);
    this.state.blockConcurrencyWhile(async () => {
      this.queue =
        (await this.state.storage.get<QueuedRequest[]>("queue")) ?? [];
      this.secretHash =
        (await this.state.storage.get<string>("secretHash")) ?? null;
    });
  }

  /** Só o path do túnel: `/_health`, `/_revoke`, `/_set-secret` e
   * `/_dispatch-job` são operações de controle internas, chamadas
   * exclusivamente pelo Worker (nunca por um client externo) — mas como
   * esta DO recebe QUALQUER request roteada pelo subdomínio
   * `{token}.vectora.chat/*` sem distinguir a origem, um path reservado
   * sozinho não bastava: um atacante que soubesse/adivinhasse o token
   * podia chamar `{token}.vectora.chat/_revoke` direto e derrubar a
   * sessão de outro usuário, `/_health` pra sondar se alguém está
   * conectado, ou `/_dispatch-job` pra mandar um "review_job" com
   * diff/metadata arbitrários pro backend local do usuário rodar — sem
   * autenticação nenhuma. GATEWAY_INTERNAL_SECRET (dedicado, nunca
   * distribuído a nenhum client — diferente do VECTORA_APP_SECRET, que
   * todo build do produto conhece) prova que a chamada veio do próprio
   * Worker, não de fora. */
  private isInternalCall(request: Request): boolean {
    const auth = request.headers.get("X-Vectora-Internal") ?? "";
    const secret = this.env.GATEWAY_INTERNAL_SECRET?.trim();
    if (!secret) return false;
    return timingSafeEqual(auth, `Bearer ${secret}`);
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);

    if (request.headers.get("Upgrade") === "websocket") {
      return this.handleWebSocketUpgrade(request);
    }

    const path = url.pathname;

    if (path === "/_health" && this.isInternalCall(request)) {
      return Response.json({
        connected: this.ws !== null,
        queued: this.queue.length,
      });
    }

    if (
      path === "/_revoke" &&
      request.method === "DELETE" &&
      this.isInternalCall(request)
    ) {
      return this.handleRevoke();
    }

    if (
      path === "/_set-secret" &&
      request.method === "POST" &&
      this.isInternalCall(request)
    ) {
      return this.handleSetSecret(request);
    }

    if (
      path === "/_dispatch-job" &&
      request.method === "POST" &&
      this.isInternalCall(request)
    ) {
      return this.handleDispatchJob(request);
    }

    // Path de controle chamado sem o header interno (ex.: alguém batendo
    // direto em `{token}.vectora.chat/_health` de fora) cai aqui de
    // propósito — vira um request tunelado comum pro backend local, que
    // não tem essa rota, então 404 lá em vez de expor estado da sessão.
    return this.forwardToLocal(request);
  }

  private async handleSetSecret(request: Request): Promise<Response> {
    const body = await request
      .json<{ secretHash?: string }>()
      .catch(() => null);
    if (!body?.secretHash) {
      return new Response("Bad Request", { status: 400 });
    }
    this.secretHash = body.secretHash;
    await this.state.storage.put("secretHash", this.secretHash);
    // Uma conexão já aberta foi autenticada com o secret ANTERIOR — trocar
    // o hash sem derrubá-la deixaria um secret comprometido continuar
    // recebendo review_job/requests até o client desconectar sozinho. O
    // client reconecta sozinho com o secret novo (mesmo backoff do resto
    // do protocolo), então fechar aqui não perde nenhum request em voo:
    // eles caem na fila normal, como qualquer desconexão.
    if (this.ws) {
      try {
        this.ws.close(1000, "secret rotated");
      } catch {
        /* ignore */
      }
      this.ws = null;
    }
    return Response.json({ ok: true });
  }

  private async handleDispatchJob(request: Request): Promise<Response> {
    const msg = await request.json<GatewayMessage>().catch(() => null);
    if (!msg || msg.type !== "review_job") {
      return new Response("Bad Request", { status: 400 });
    }
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return Response.json({ ok: true, delivered: false });
    }
    this.ws.send(JSON.stringify(msg));
    return Response.json({ ok: true, delivered: true });
  }

  private async handleWebSocketUpgrade(request: Request): Promise<Response> {
    // `secretHash` só existe depois de um `/register` completo (que chama
    // `/_set-secret` antes de devolver o token pro client) — uma sessão sem
    // ele nunca terminou o registro, seja por nunca ter existido, seja por
    // um `token` adivinhado/reciclado. Nenhum caso legítimo de conexão passa
    // por aqui sem secretHash: sempre rejeitar.
    const auth = request.headers.get("Authorization") ?? "";
    const presented = auth.startsWith("Bearer ") ? auth.slice(7) : "";
    const presentedHash = presented ? await hashConnectorSecret(presented) : "";
    if (
      !this.secretHash ||
      !presentedHash ||
      !timingSafeEqual(presentedHash, this.secretHash)
    ) {
      return new Response("Unauthorized", { status: 401 });
    }

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair) as [WebSocket, WebSocket];

    this.state.acceptWebSocket(server);
    this.replaceConnection(server);

    return new Response(null, { status: 101, webSocket: client });
  }

  private replaceConnection(ws: WebSocket): void {
    if (this.ws) {
      try {
        this.ws.close(1001, "replaced");
      } catch {
        /* ignore */
      }
    }
    this.ws = ws;

    if (this.pingTimer) clearInterval(this.pingTimer);
    this.pingTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        const msg: GatewayMessage = { type: "ping" };
        this.ws.send(JSON.stringify(msg));
      }
    }, PING_INTERVAL_MS);

    this.flushQueue();
  }

  async webSocketMessage(
    ws: WebSocket,
    message: string | ArrayBuffer,
  ): Promise<void> {
    if (typeof message !== "string") return;
    let parsed: ClientMessage;
    try {
      parsed = JSON.parse(message) as ClientMessage;
    } catch {
      return;
    }

    if (parsed.type === "response") {
      const pending = this.pending.get(parsed.id);
      if (pending) {
        clearTimeout(pending.timer);
        this.pending.delete(parsed.id);
        pending.resolve(parsed);
      }
    }
  }

  async webSocketClose(_ws: WebSocket): Promise<void> {
    this.ws = null;
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
    // Rejeita todos os pending em voo
    for (const [id, p] of this.pending) {
      clearTimeout(p.timer);
      p.reject(new Error("WebSocket closed"));
      this.pending.delete(id);
    }
  }

  async webSocketError(_ws: WebSocket, _error: unknown): Promise<void> {
    await this.webSocketClose(_ws);
  }

  private async forwardToLocal(request: Request): Promise<Response> {
    const maxBytes = parseInt(this.env.MAX_PAYLOAD_BYTES ?? "5242880", 10);
    const contentLength = parseInt(
      request.headers.get("content-length") ?? "0",
      10,
    );
    if (contentLength > maxBytes) {
      return new Response("Payload Too Large", { status: 413 });
    }

    const bodyBytes = await request.arrayBuffer();
    if (bodyBytes.byteLength > maxBytes) {
      return new Response("Payload Too Large", { status: 413 });
    }

    const bodyB64 = btoa(String.fromCharCode(...new Uint8Array(bodyBytes)));
    const headers: Record<string, string> = {};
    request.headers.forEach((v, k) => {
      headers[k] = v;
    });

    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      await this.enqueue(
        request.method,
        new URL(request.url).pathname,
        headers,
        bodyB64,
      );
      return new Response("Accepted — backend offline, request queued", {
        status: 202,
      });
    }

    const id = crypto.randomUUID();
    const msg: GatewayMessage = {
      type: "request",
      id,
      method: request.method,
      path: new URL(request.url).pathname + new URL(request.url).search,
      headers,
      body: bodyB64,
    };

    return new Promise<Response>((resolve) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        resolve(new Response("Gateway Timeout", { status: 504 }));
      }, FORWARD_TIMEOUT_MS);

      this.pending.set(id, {
        resolve: (resp) => {
          const respHeaders = new Headers(resp.headers);
          const respBody = Uint8Array.from(atob(resp.body), (c) =>
            c.charCodeAt(0),
          );
          resolve(
            new Response(respBody, {
              status: resp.status,
              headers: respHeaders,
            }),
          );
        },
        reject: () => resolve(new Response("Bad Gateway", { status: 502 })),
        timer,
      });

      this.ws!.send(JSON.stringify(msg));
    });
  }

  private async enqueue(
    method: string,
    path: string,
    headers: Record<string, string>,
    body: string,
  ): Promise<void> {
    const now = Date.now();
    this.queue = this.queue.filter((q) => now - q.enqueuedAt < this.ttlMs);
    this.queue.push({
      id: crypto.randomUUID(),
      method,
      path,
      headers,
      body,
      enqueuedAt: now,
    });
    await this.state.storage.put("queue", this.queue);
    await this.state.storage.setAlarm(now + this.ttlMs + 1000);
  }

  async alarm(): Promise<void> {
    const now = Date.now();
    this.queue = this.queue.filter((q) => now - q.enqueuedAt < this.ttlMs);
    await this.state.storage.put("queue", this.queue);
  }

  private flushQueue(): void {
    if (!this.ws || this.queue.length === 0) return;
    const msg: GatewayMessage = { type: "queued", items: [...this.queue] };
    this.ws.send(JSON.stringify(msg));
    this.queue = [];
    void this.state.storage.put("queue", this.queue);
  }

  private handleRevoke(): Response {
    if (this.ws) {
      try {
        this.ws.close(1000, "revoked");
      } catch {
        /* ignore */
      }
      this.ws = null;
    }
    this.queue = [];
    // Persiste limpeza da fila na próxima escrita (próximo enqueue ou alarm)
    return Response.json({ revoked: true });
  }
}

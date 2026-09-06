/** Serializes one-time OAuth result reads per state. */
export class OAuthResult implements DurableObject {
  constructor(private readonly state: DurableObjectState) {}

  async fetch(request: Request): Promise<Response> {
    const key = new URL(request.url).searchParams.get("key");
    if (!key) return Response.json({ error: "key_required" }, { status: 400 });
    if (request.method === "POST") {
      const payload = (await request.json()) as {
        value: string;
        expirationTtl: number;
      };
      await this.state.storage.put(key, {
        value: payload.value,
        expiresAt: Date.now() + payload.expirationTtl * 1000,
      });
      await this.state.storage.setAlarm(
        Date.now() + payload.expirationTtl * 1000,
      );
      return Response.json({ ok: true });
    }
    if (request.method === "DELETE") {
      await this.state.storage.delete(key);
      return Response.json({ ok: true });
    }
    const entry = await this.state.storage.get<{
      value: string;
      expiresAt: number;
    }>(key);
    if (!entry || entry.expiresAt <= Date.now()) {
      await this.state.storage.delete(key);
      return new Response(null, { status: 202 });
    }
    const expectedProvider = new URL(request.url).searchParams.get("provider");
    if (expectedProvider) {
      const parsed = JSON.parse(entry.value) as { provider?: string };
      if (parsed.provider !== expectedProvider) {
        return Response.json({ error: "provider_mismatch" }, { status: 409 });
      }
    }
    if (new URL(request.url).searchParams.get("consume") !== "false") {
      await this.state.storage.delete(key);
    }
    return Response.json(JSON.parse(entry.value));
  }

  async alarm(): Promise<void> {
    const now = Date.now();
    const entries = await this.state.storage.list<{
      value: string;
      expiresAt: number;
    }>();
    let nextExpiration: number | null = null;
    for (const [key, entry] of entries) {
      if (entry.expiresAt <= now) {
        await this.state.storage.delete(key);
      } else if (nextExpiration === null || entry.expiresAt < nextExpiration) {
        nextExpiration = entry.expiresAt;
      }
    }
    if (nextExpiration !== null)
      await this.state.storage.setAlarm(nextExpiration);
  }
}

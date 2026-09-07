import { describe, expect, it } from "vitest";
import { OAuthResult } from "../../src/oauth/oauth-result";

describe("OAuthResult", () => {
  it("mantém o menor vencimento ao gravar múltiplas chaves", async () => {
    const values = new Map<string, unknown>();
    let alarm: number | null = null;
    const storage = {
      put: async (key: string, value: unknown) => void values.set(key, value),
      get: async <T>(key: string) => values.get(key) as T | undefined,
      delete: async (key: string) => void values.delete(key),
      getAlarm: async () => alarm,
      setAlarm: async (value: number) => void (alarm = value),
      list: async () => values,
    };
    const object = new OAuthResult({
      storage,
    } as unknown as DurableObjectState);
    const now = Date.now();

    await object.fetch(
      new Request("https://oauth-result/?key=late", {
        method: "POST",
        body: JSON.stringify({
          value: '{"provider":"github"}',
          expirationTtl: 300,
        }),
      }),
    );
    const lateAlarm = alarm;
    await object.fetch(
      new Request("https://oauth-result/?key=early", {
        method: "POST",
        body: JSON.stringify({
          value: '{"provider":"github"}',
          expirationTtl: 30,
        }),
      }),
    );

    expect(alarm).not.toBeNull();
    expect(alarm!).toBeLessThan(lateAlarm!);
    expect(alarm!).toBeGreaterThanOrEqual(now);
  });
});

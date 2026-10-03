import { chromium } from "@playwright/test";
import { createServer } from "node:http";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { browserFramePermissions } from "../browser-frame-policy";

const server = createServer((_request, response) => {
  response.setHeader("Content-Type", "text/html");
  response.end("<!doctype html><title>Permission fixture</title>");
});
let fixtureOrigin: string;
beforeAll(async () => {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string")
    throw new Error("missing test port");
  fixtureOrigin = `http://127.0.0.1:${address.port}`;
});
afterAll(async () => {
  await new Promise<void>((resolve, reject) =>
    server.close((error) => (error ? reject(error) : resolve())),
  );
});

describe("web iframe permissions in Chromium", () => {
  it("enforces denial, grants trusted-frame geolocation, then revokes on replacement", async () => {
    const browser = await chromium.launch({ headless: true });
    try {
      const context = await browser.newContext({
        permissions: ["geolocation"],
        geolocation: { latitude: 1, longitude: 2 },
      });
      const page = await context.newPage();
      await page.goto(fixtureOrigin);
      for (const allowed of [false, true, false]) {
        await page.evaluate(
          ({ origin, policy }) => {
            document.querySelector("iframe")?.remove();
            const frame = document.createElement("iframe");
            frame.allow = policy;
            frame.sandbox.add("allow-scripts", "allow-same-origin");
            frame.src = origin + "/frame";
            document.body.append(frame);
          },
          { origin: fixtureOrigin, policy: browserFramePermissions(allowed) },
        );
        const frame = page.frameLocator("iframe");
        await frame.locator("body").waitFor({ state: "attached" });
        const result = await frame.locator("body").evaluate(
          () =>
            new Promise<boolean>((resolve) =>
              navigator.geolocation.getCurrentPosition(
                () => resolve(true),
                () => resolve(false),
                { timeout: 2000 },
              ),
            ),
        );
        expect(result).toBe(allowed);
      }
      // Delegation can grant geolocation without granting access to the parent DOM.
      await page.evaluate(
        ({ origin, policy }) => {
          document.querySelector("iframe")?.remove();
          const frame = document.createElement("iframe");
          frame.allow = policy;
          frame.sandbox.add("allow-scripts");
          frame.src = origin + "/opaque";
          document.body.append(frame);
        },
        { origin: fixtureOrigin, policy: browserFramePermissions(true) },
      );
      const opaque = page.frameLocator("iframe");
      await opaque.locator("body").waitFor({ state: "attached" });
      expect(
        await opaque.locator("body").evaluate(
          () =>
            new Promise<boolean>((resolve) =>
              navigator.geolocation.getCurrentPosition(
                () => resolve(true),
                () => resolve(false),
                { timeout: 2000 },
              ),
            ),
        ),
      ).toBe(true);
      expect(
        await opaque.locator("body").evaluate(() => {
          try {
            void window.parent.document.body;
            return false;
          } catch {
            return true;
          }
        }),
      ).toBe(true);
    } finally {
      await browser.close();
    }
  }, 30000);
});

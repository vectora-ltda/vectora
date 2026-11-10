import { chromium, expect, test as base } from "@playwright/test";
import type { BrowserContext, Page } from "@playwright/test";

const endpoint =
  process.env.PLAYWRIGHT_CDP_URL ??
  `http://127.0.0.1:${process.env.VECTORA_ELECTRON_CDP_PORT ?? "9222"}`;

/** Playwright controla a janela do Electron pelo CDP; não lança Chromium próprio. */
export const test = base.extend<{
  context: BrowserContext;
  page: Page;
}>({
  browser: async (_fixtures, use) => {
    const browser = await chromium.connectOverCDP(endpoint);
    await use(browser);
    await browser.close();
  },
  context: async ({ browser }, use) => {
    const context = browser.contexts()[0];
    if (!context) {
      throw new Error(`Nenhum contexto Electron disponível em ${endpoint}`);
    }
    await use(context);
  },
  page: async ({ context }, use) => {
    const page = context.pages()[0] ?? (await context.newPage());
    await use(page);
  },
});

export { expect, type Page };

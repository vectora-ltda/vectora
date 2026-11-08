import { defineConfig } from "@playwright/test";

/**
 * Playwright — testes E2E no Chromium que acompanha o Electron do Vectora.
 *
 * Pré-requisitos: Vectora desktop rodando com CDP habilitado e as credenciais
 * E2E disponíveis via E2E_EMAIL / E2E_PASSWORD.
 *
 * Playwright conecta via CDP ao Electron já iniciado e navega pela origem
 * vectora-app://app/, servida pelo próprio app.
 */

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? "vectora-app://app/";

export default defineConfig({
  testDir: "./e2e",
  // Respostas reais de LLM podem demorar; damos folga por teste e por ação.
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    video: "retain-on-failure",
  },
});

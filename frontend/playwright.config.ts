import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright — testes e2e de browser real do chat do Vectora.
 *
 * Pré-requisitos para rodar (`pnpm --dir frontend test:e2e`):
 *  - Backend do Vectora rodando em http://127.0.0.1:8080 (com GOOGLE_API_KEY
 *    ou outra chave de provider configurada — os testes exercitam o LLM real).
 *  - Credenciais e2e via env: E2E_EMAIL / E2E_PASSWORD (o global-setup cria o
 *    usuário root na primeira execução se ainda não houver usuários).
 *
 * O dev server do Vite (porta 5173) é iniciado automaticamente (webServer) e
 * faz proxy de /auth e /vectora para o backend.
 */

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";

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
    storageState: "./e2e/.auth/state.json",
    trace: "on-first-retry",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "pnpm dev",
    url: BASE_URL,
    reuseExistingServer: true,
    timeout: 120_000,
  },
});

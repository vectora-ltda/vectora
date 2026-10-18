import { defineConfig } from "vitest/config";

/**
 * Config standalone pro teste de update real contra produção
 * (tests/scripts/live-update.test.ts) — deliberadamente FORA dos `projects`
 * de vitest.config.mts (o config default do `test`/`scons tests`), pra
 * nunca rodar como parte da suíte hermética de CI. Roda sob demanda:
 *
 *   pnpm --dir services exec vitest run --config vitest.live.config.mts
 */
export default defineConfig({
  test: {
    reporters: ["default"],
    include: [
      "tests/scripts/live-update.test.ts",
      "tests/gateway/gateway-live.test.ts",
    ],
    // Default do Vitest (5s) é curto pra requests contra rede/produção
    // real — generoso o bastante pra não ficar flaky em latência normal,
    // sem mascarar uma falha de verdade (endpoint fora do ar já erra bem
    // antes disso).
    testTimeout: 30_000,
  },
});

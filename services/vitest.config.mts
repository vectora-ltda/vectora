import { cloudflarePool, cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

const workerOptions = {
  // wrangler.test.toml, não wrangler.toml — ver comentário nesse arquivo.
  wrangler: { configPath: "./wrangler.test.toml" },
  miniflare: {
    bindings: {
      VECTORA_APP_SECRET: "test-app-secret-fixo-por-produto",
      GATEWAY_INTERNAL_SECRET: "test-internal-secret-worker-only",
      GATEWAY_HMAC_SECRET: "test-hmac-secret-32-chars-minimum",
      VECTORA_OAUTH_SECRET: "test-oauth-secret",
      APP_URL: "https://vectora.company",
      GATEWAY_URL: "https://gateway.vectora.chat",
      RESEND_API_KEY: "test-resend-key",
      STRIPE_SECRET_KEY: "sk_test_fake",
      STRIPE_WEBHOOK_SECRET: "whsec_test_fake",
      STRIPE_PRICE_PRO_USD: "price_test_fake",
      ASAAS_API_KEY: "test-asaas-key",
      ASAAS_API_URL: "https://api.asaas.com/v3",
      ASAAS_WEBHOOK_SECRET: "test-asaas-webhook-secret",
      // Turnstile desligado nos testes: verifyTurnstile só dispensa a checagem
      // com secret vazio/ausente. Fixar aqui mantém os testes herméticos — sem
      // este pin, o TURNSTILE_SECRET_KEY real do `.env` do dev vaza pro worker
      // de teste e a verificação do token fake ("test-token") bate no
      // siteverify e falha (signup/login/issues/waitlist viram 400).
      TURNSTILE_SECRET_KEY: "",
      // 32 bytes fixos (não usado em produção) — só pros testes de
      // gha-bot/crypto.ts terem uma chave AES-256-GCM válida e estável.
      GHA_BOT_ENCRYPTION_KEY: "BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc=",
      // workerd SQLite permanece bloqueado no Windows após o fetch ao DO,
      // impedindo o cleanup do isolated storage. Marca para skip seletivo.
      TEST_IS_WINDOWS: process.platform === "win32" ? "1" : "0",
    },
    kvNamespaces: ["GATEWAY_METRICS", "KV"],
    r2Buckets: ["R2"],
    d1Databases: ["DB"],
    durableObjects: {
      GATEWAY_SESSION: "GatewaySession",
    },
    queueProducers: {
      EMAIL_QUEUE: "vectora-email",
      JOBS_QUEUE: "vectora-jobs",
    },
    queueConsumers: ["vectora-email", "vectora-jobs"],
  },
};

export default defineConfig({
  // `scripts/` (release.ts) é um CLI Node puro — nunca roda dentro do
  // Worker, só localmente via tsx. Rodar seu teste no pool workerd (isolate
  // sandboxed, sem child_process/fs de verdade) quebra o teardown do
  // miniflare (WebSocket não fecha, "close timed out"). Cada projeto usa o
  // runtime certo pro que está testando.
  test: {
    reporters: ["dot"],
    // Istanbul, não V8 — o coverage nativo do vitest não instrumenta workerd.
    // `gateway-session.ts` fica perto de 0% quando rodado no Windows: os
    // testes que tocam o Durable Object (itDO em gateway-session.test.ts)
    // são pulados aqui pelo mesmo motivo do skip seletivo (SQLite do DO
    // trava o cleanup do isolated storage no Windows) — em CI (Linux) esses
    // testes rodam e a cobertura reflete o arquivo inteiro.
    coverage: {
      provider: "istanbul",
      reporter: ["text", "html"],
      include: ["src/**/*.ts"],
    },
    projects: [
      {
        plugins: [cloudflareTest(workerOptions)],
        test: {
          name: "workers",
          include: ["tests/**/*.test.ts"],
          exclude: ["tests/scripts/**"],
          setupFiles: ["./tests/test-setup.ts"],
          pool: cloudflarePool(workerOptions),
        },
      },
      {
        test: {
          name: "node",
          include: ["tests/scripts/**/*.test.ts"],
          // Depende de rede/produção real (services.vectora.company) — nunca
          // roda como parte de `scons tests`/CI hermético. Config própria
          // (vitest.live.config.mts), sob demanda: ver o cabeçalho do
          // arquivo pro comando exato.
          exclude: ["tests/scripts/live-update.test.ts"],
        },
      },
    ],
  },
});

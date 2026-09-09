# vectora-services

Worker único no Cloudflare que serve `gateway/`, `updates/` e
auth/billing/license/GDPR/api-keys/issues/rag-library/registry. Domínios:

- `gateway.vectora.chat` + `{token}.vectora.chat` — proxy WebSocket
  bidirecional de OAuth/webhooks pro app desktop (`src/gateway/`, ex-relay —
  renomeado sem alias de transição, decisão do produto: não havia clientes
  antigos em produção pra coordenar).
- `services.vectora.company` — tudo o mais montado num único Hono app em
  `src/index.ts`: auth/billing/license/GDPR/api-keys/issues/rag-library/
  registry/telemetry **e** updates (distribuição de releases pro
  `electron-updater` + download público de primeira instalação,
  `src/updates/`) — mesclado na raiz via `.route("/", updatesApp)`, sem
  domínio próprio.

Dispatch por hostname em `src/index.ts`: só gateway tem host dedicado;
qualquer outro host cai no app único de `services.vectora.company`.

**Decisão de arquitetura**: `src/gateway/index.ts` é um `ExportedHandler`
cru (roteamento manual por `startsWith`/`slice` de path), não um sub-app
Hono como todo o resto do repo — upgrade de WebSocket + Durable Object não
combina bem com o roteador padrão do Hono. Intencional, não um desvio de
padrão a corrigir.

## Rotas

**gateway** (`gateway.vectora.chat`):

- `POST /register` — autenticado por `VECTORA_APP_SECRET` (secret fixo por
  produto, embutido no binário Nuitka — igual pra toda instalação) via
  `Authorization: Bearer`; troca o fingerprint da máquina por um token de
  gateway de 6 caracteres + URL de WebSocket.
- `GET|POST /ws/:token` — upgrade pra WebSocket, vira a sessão ativa da
  Durable Object `GatewaySession`.
- `GET /health/:token` — `{connected, queued}`.
- `DELETE /gateway/session/:token` — revoga a sessão.
- `POST /oauth/token` / `GET /oauth/token/:state` — device flow de OAuth
  (a company grava o token trocado, o backend consome via polling).

**gateway** (`{token}.vectora.chat`, qualquer path): webhooks/callbacks OAuth
de terceiros (GitHub etc.) — encaminhados pro backend local via WebSocket se
conectado, ou enfileirados (TTL 10min) se offline.

**updates** (`services.vectora.company`, mesclado na raiz — sem prefixo):

- `GET /updates/:channel/:os/:arch/latest.yml` — manifesto do
  electron-updater (sem token — Free não tem conta).
- `GET /updates/:channel/:os/:arch/:version/:filename` — binário/blockmap.
- `GET /download/:channel/:target` (`:target` = `win-x64.exe`, `mac-arm64.dmg`
  etc.) — download de primeira instalação (sem token, sem rollout — sempre a
  versão estável do canal).
- `POST /telemetry/update-result` — só enfileira (job `update_telemetry`); a
  contagem de sucesso/falha e a quarentena automática após 3 falhas em 1h
  rodam no consumer da fila `vectora-jobs` (`processUpdateTelemetry`).

**services** (`services.vectora.company`) — Hono, um router por
responsabilidade, tudo sobre D1 (sem RLS — autorização é código, em cada
handler):

- `/auth/*` — signup, login, logout, refresh, verificação de email.
- `/profile/*` — dados de perfil da conta.
- `/billing/*` — checkout/portal/webhooks (Stripe INTL + Asaas BR), sessão
  web autenticada por cookie.
- `/license/*` — validação/rotação de `VECTORA_TOKEN`, `agent-login`
  (email+senha → token, usado pelo backend Python), `portal` (mesma lógica
  de `/billing/portal`, mas autenticado pelo token em vez de sessão — é a
  rota que o backend Python chama).
- `/oauth/*` — device flow que conecta a conta vectora.company ao gateway.
- `/gdpr/*` — soft-delete + Cron Trigger diário que só enfileira 1 job
  `gdpr_delete_user` por usuário expirado (hard-delete de verdade acontece
  no consumer, `hardDeleteOneUser`).
- `/api-keys/*`, `/issues/*` — gestão de chaves e tickets de suporte.
- `/rag-library/*` — catálogo + download de bancos RAG pré-indexados (Fase E
  segue fora de escopo — nenhum pacote real ainda). `POST /:id/reindex`
  enfileira de verdade (`rag_reindex`), mas o consumer sempre marca
  `status='failed'`: não existe provedor de storage externo configurado.
- `/registry/*` — `mcp`, `skills`, `extensions` — um registry com catálogos
  relacionados. `mcp` já lê D1 de
  verdade (6 conectores curados no seed + discovery automático). `skills`
  também lê D1 e agora aceita publish da comunidade (`POST /skills`,
  autenticado, `verified=0` até curadoria via `PATCH
/admin/skills/:id/verify`) — lista vazia continua sendo estado válido até
  a primeira publicação/curadoria. `?q=`/`?category=`/`?tags=` filtram os
  dois catálogos. Só `extensions` continua placeholder (`{entries: []}`) —
  depende do SDK de extensões, que ainda não existe.
- `/telemetry/ingest` — ingestão genérica de eventos do backend Python local
  (sem auth — Free não tem conta); só enfileira (`telemetry_ingest`), grava
  em `telemetry_events` no consumer.

## Bindings (`wrangler.toml`)

- Durable Object `GATEWAY_SESSION` (classe `GatewaySession`).
- KV `GATEWAY_METRICS` — estado de OAuth device flow do gateway.
- R2 `R2` (bucket `vectora-r2`) — instaladores + manifestos.
- KV `KV` — config de canais/rollout/quarentena do updates.
- D1 `DB` (`vectora-db`) — users/sessions/tokens/subscriptions/license_checks/
  payment_events/email_verifications/rag_packages/telemetry_events
  (`migrations/`).
- Cron Trigger diário (`[triggers]`) — GDPR (enfileira, não deleta direto).
- Queue `vectora-email` (producer `EMAIL_QUEUE`) — todo `sendEmail` real
  passa por aqui; consumer em `src/queue-consumer.ts`, DLQ
  `vectora-email-dlq`.
- Queue `vectora-jobs` (producer `JOBS_QUEUE`, `max_concurrency = 1`,
  proposital — serializa a telemetria de update, matando a race condition
  que existia no read-modify-write direto em KV) — jobs `gdpr_delete_user`,
  `update_telemetry`, `telemetry_ingest`, `rag_reindex`; DLQ
  `vectora-jobs-dlq`.

### Atualização do schema de comentários do GitHub

Bancos D1 que aplicaram uma versão anterior da sincronização de issues podem
não possuir as colunas `updated_at` e `deleted_at` em `issue_comments`. Antes
de publicar o worker com a reconciliação de comentários, verifique o schema e
execute uma vez, no banco remoto, os comandos abaixo para cada coluna ausente:

```bash
wrangler d1 execute vectora-db --remote --command "ALTER TABLE issue_comments ADD COLUMN updated_at TEXT"
wrangler d1 execute vectora-db --remote --command "ALTER TABLE issue_comments ADD COLUMN deleted_at TEXT"
```

O comando deve ser executado somente quando a coluna correspondente ainda não
existir; confirme com `wrangler d1 execute vectora-db --remote --command
\"PRAGMA table_info(issue_comments)\"` antes de aplicar.

- Secrets (via `wrangler secret put`, não no `.toml`): `VECTORA_APP_SECRET`
  (secret fixo por produto, autentica `POST /register`), `GATEWAY_HMAC_SECRET`,
  `VECTORA_OAUTH_SECRET` (gateway); `RESEND_API_KEY` (email transacional);
  `TURNSTILE_SECRET_KEY` (anti-bot); `STRIPE_SECRET_KEY`,
  `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PRO_USD` (billing INTL);
  `ASAAS_API_KEY`, `ASAAS_API_URL`, `ASAAS_WEBHOOK_SECRET` (billing BR);
  `GHA_BOT_ENCRYPTION_KEY` (gha-bot, chave mestra AES-256-GCM);
  `GITHUB_TOKEN` (registry discovery, opcional).

## Publicar um release

Depois de `scons release-<os>` gerar os instaladores em
`vectora/frontend/dist-electron/`:

```powershell
pnpm run release -- --version=X.Y.Z
```

Sobe pra R2 e atualiza o canal `latest` no KV (`scripts/release.ts`).

## Testes

`@cloudflare/vitest-pool-workers` (miniflare real, não mocks manuais) —
`pnpm test`. Alguns testes de Durable Object são pulados no Windows
(`TEST_IS_WINDOWS=1`) por um lock de SQLite do workerd que não libera antes
do cleanup do isolated storage; rodam normalmente em CI (Linux).

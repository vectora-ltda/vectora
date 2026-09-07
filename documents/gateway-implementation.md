# Vectora Gateway — Arquitetura e Implementação

> Este documento descreve o estado real do Worker `vectora-services`
> (`services/src/`) e da integração com o backend Python
> (`vectora/backend/`). O produto roda num Worker Cloudflare único com D1 —
> não há Supabase nem Worker de gateway isolado. Ver `documents/history.md`
> e `documents/business-model.md` ("Billing, auth e licenciamento —
> arquitetura atual") pro resumo executivo de produto.

---

Esta seção define o fluxo de autorização das integrações gerenciado pela
Vectora LTDA: o Worker mantém os OAuth Apps e o backend local apenas inicia o
fluxo e consome o resultado autenticado.

## OAuth de integrações

Os OAuth Apps das integrações são registrados pela Vectora LTDA no Worker
`vectora-services`. O desktop não recebe client secret e a interface não pede
que o usuário crie um app próprio. O backend gera um state aleatório e inicia
o broker; o callback público do Worker troca o code, grava o resultado
temporariamente no Durable Object SQLite `OAUTH_RESULT` (migration v3), com TTL físico de cinco minutos, e redireciona apenas o state
para o subdomínio do gateway. O backend consulta o resultado uma única vez com
`VECTORA_OAUTH_SECRET` e grava o token no override do usuário. Redirects são
aceitos somente em `https://*.vectora.chat` e o valor do token nunca aparece
na URL, no binário ou nos logs.

## Arquitetura — O que é o gateway e quem faz o quê

```text
Vectora LTDA (operador)
  └── registra UMA vez os OAuth Apps no GitHub/Google/GitLab e configura os secrets no Worker

Usuário final do Vectora (instala o .exe)
  └── não configura nada de gateway/OAuth — tudo acontece automaticamente
  └── só autoriza sua conta GitHub/Google/GitLab dentro do app Vectora

vectora-services (Worker Cloudflare único — services/src/index.ts)
  └── dispatch por hostname: gateway.vectora.chat + {token}.vectora.chat → gateway;
      qualquer outro host → o app da company (auth/billing/license/gdpr/...)
  └── gateway: recebe conexões WebSocket de backends Vectora
  └── atribui token estável: HMAC-SHA256(fingerprint) → 6 chars (base36)
  └── {token}.vectora.chat é o subdomínio DESSA instalação — requests de
      túnel e webhooks são serializados e encaminhados pelo WebSocket ativo
      pro backend local (proxy HTTP genérico, não rotas hardcoded por provider);
      callbacks do broker OAuth são tratados diretamente em services.vectora.company
```

**Um único Worker, dois domínios servidos por ele** (`services/src/index.ts`):

- `gateway.vectora.chat` + `*.vectora.chat` (zona `vectora.chat`, única `[[routes]]` do `wrangler.toml`) → o gateway (device/session relay pro desktop).
- `services.vectora.company` → o mesmo Worker, branch "qualquer outro host": auth/admin/profile/billing/license/oauth (device flow)/gdpr/api-keys/issues/rag-library/registry/telemetry + updates (`services/src/index.ts`, um único `Hono` app montado por prefixo). **Este segundo domínio não tem `[[routes]]` própria no `wrangler.toml`** — a exposição em `services.vectora.company` é configurada como Custom Domain fora deste repo (validar com quem administra o Cloudflare antes de assumir o mecanismo exato).
- O **site** `vectora.company` (marketing/dashboard, TanStack Start) é outro sistema por completo — hospedado no **Vercel** (`company/vercel.json`), não neste Worker.

**Dois tipos de OAuth — não confundir:**

| Tipo                     | Propósito                                     | Provider                                   | Callback                                                                  |
| ------------------------ | --------------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------- |
| **Login na company**     | Entrar em vectora.company                     | `services` (D1, sessão própria)            | tratado no próprio `services.vectora.company`                             |
| **Integração do agente** | Agente acessa GitHub/GitLab/Google do usuário | Provider → services OAuth broker → Backend | `https://services.vectora.company/oauth/integrations/{provider}/callback` |

Slack permanece fora do broker centralizado e usa o fluxo local Socket Mode,
com `SLACK_BOT_TOKEN` e `SLACK_APP_TOKEN` configurados pela integração local.

A Seção 3 deste plano é sobre o **segundo tipo** — OAuth para que o agente faça chamadas API em nome do usuário.

---

## SEÇÃO 1 — Cloudflare

### 1.1 Worker `vectora-services`

`services/wrangler.toml`: `name = "vectora-services"`, `main = "src/index.ts"`, `compatibility_date = "2025-06-01"`, `nodejs_compat` habilitado.

### 1.2 Rota

Uma única entrada em `[[routes]]`:

```toml
[[routes]]
pattern = "*.vectora.chat/*"
zone_name = "vectora.chat"
```

O wildcard cobre `gateway.vectora.chat` (host fixo do gateway) e qualquer `{token}.vectora.chat` (subdomínio por instalação) — não são duas entradas separadas, é uma única regra que o Worker desambigua internamente (`services/src/gateway/index.ts`, comparando `host` contra `GATEWAY_HOST`).

> Registrar `gateway.vectora.chat` como Custom Domain **em paralelo** a essa rota conflita na API da Cloudflare (comentário explícito no `wrangler.toml`) — não faça as duas coisas.

### 1.3 Bindings do Worker (`services/wrangler.toml`)

| Binding                    | Tipo                  | Nome real                                   | Uso                                                                |
| -------------------------- | --------------------- | ------------------------------------------- | ------------------------------------------------------------------ |
| `DB`                       | D1                    | `vectora-db`                                | Substitui o Postgres do Supabase — sem RLS, autorização em código  |
| `R2`                       | R2 bucket             | `vectora-r2`                                | Releases (updates) + exports GDPR                                  |
| `GATEWAY_SESSION`          | Durable Object        | classe `GatewaySession`                     | Uma instância por token/instalação, relay WebSocket↔HTTP           |
| `GATEWAY_METRICS`          | KV                    | id `f38a1de6…`                              | Estado do OAuth device-flow do gateway (`oauth:{state}` → token)   |
| `OAUTH_RESULT`             | Durable Object SQLite | classe `OAuthResult`, migration v3          | Estado e resultado OAuth de integração, TTL físico e consumo único |
| `KV`                       | KV                    | id `0bed7e9f…`                              | Config de canais/rollout/quarentena de updates                     |
| `EMAIL_QUEUE`              | Queue                 | `vectora-email` (+ DLQ)                     | Envio de email assíncrono (Resend)                                 |
| `JOBS_QUEUE`               | Queue                 | `vectora-jobs` (+ DLQ, `max_concurrency=1`) | Jobs em background (ex.: hard-delete GDPR agendado)                |
| `LICENSE_VALIDATE_LIMITER` | Rate limit            | 30 req/min                                  | `POST /license/validate` (endpoint público)                        |

Cron: `0 3 * * *` (diário) dispara o hard-delete de contas GDPR expiradas há 30+ dias.

### 1.4 Secrets no Worker

Executar em `services/` (nomes **sem mudança** em relação à versão anterior deste doc):

```powershell
# 1. HMAC para gerar tokens estáveis por instalação (só o gateway usa)
#    Gerar: python -c "import secrets; print(secrets.token_hex(32))"
pnpm wrangler secret put GATEWAY_HMAC_SECRET

# 2. Segredo compartilhado company ↔ gateway ↔ backend, device flow de licença
pnpm wrangler secret put VECTORA_OAUTH_SECRET

# 3. Prova que o cliente é um build genuíno do Vectora (fixo por produto,
#    embutido no binário Nuitka — não é por usuário)
pnpm wrangler secret put VECTORA_APP_SECRET

# 4. Billing (Stripe internacional + Asaas Brasil)
pnpm wrangler secret put STRIPE_SECRET_KEY
pnpm wrangler secret put STRIPE_WEBHOOK_SECRET
pnpm wrangler secret put STRIPE_PRICE_PRO_USD
pnpm wrangler secret put ASAAS_API_KEY
pnpm wrangler secret put ASAAS_API_URL

# 5. Email, cadastro
pnpm wrangler secret put RESEND_API_KEY
pnpm wrangler secret put TURNSTILE_SECRET_KEY
```

> Não existe `VECTORA_JWT_SECRET` nem nunca existiu como secret de produção real — sessão da company usa **token opaco** (D1, hash SHA-256), não JWT (ver Seção 5).

---

## SEÇÃO 2 — Backend Vectora App

### 2.1 Variáveis de Ambiente do Gateway no Backend

Em `~/.vectora/.env` da instalação (defaults em `backend/settings.py`/`backend/defaults.env`):

```env
GATEWAY_URL=wss://gateway.vectora.chat
GATEWAY_ENABLED=true

# Compartilhado com o Worker para OAuth device flow de licença
VECTORA_OAUTH_SECRET=<mesmo valor do wrangler secret VECTORA_OAUTH_SECRET>

# Embutido no build, não precisa ser configurado manualmente em instalação normal
VECTORA_APP_SECRET=<mesmo valor do wrangler secret VECTORA_APP_SECRET>
```

Outras URLs configuráveis (default já aponta pra produção; útil pra apontar a um `services` local via `wrangler dev` ou self-hosted): `VECTORA_LICENSE_URL`, `VECTORA_LICENSE_CONNECT_URL`, `VECTORA_LICENSE_PORTAL_URL`, `VECTORA_COMPANY_URL`, `VECTORA_GATEWAY_URL` (ver `.env.example`).

### 2.2 Como o Backend Registra com o Gateway

```
Backend                              Gateway (Worker)
  │                                    │
  │  POST /register                    │
  │  Authorization: Bearer <APP_SECRET>│
  │  { fingerprint }                   │
  │ ─────────────────────────────────► │
  │                                    │  timingSafeEqual contra
  │                                    │  env.VECTORA_APP_SECRET
  │                                    │  token = HMAC-SHA256(fingerprint)[:4B] → base36, 6 chars
  │  { token, subdomain, websocket_url }│
  │ ◄───────────────────────────────── │
  │                                    │
  │  WebSocket: wss://gateway.vectora.chat/ws/{token} │
  │ ─────────────────────────────────► │
```

O token é salvo em `~/.vectora/gateway_token` (`backend/services/gateway/token.py`, permissões de arquivo restritas). O `GatewayClient` mantém o WebSocket vivo com backoff exponencial; se cair, o Worker enfileira requests recebidos por até 10 minutos (`QUEUE_TTL_MS`) e reenvia tudo de uma vez na reconexão.

O subdomínio `{token}.vectora.chat` aparece em `GET /gateway/status` no backend para exibir ao usuário no dashboard do app.

**Importante — mecanismo de proxy e broker OAuth**: o Worker encaminha requests de túnel em `{token}.vectora.chat/*` para o backend, mas os callbacks OAuth centralizados são tratados diretamente pelo broker em `/oauth/integrations/{provider}/callback`. O `GatewayClient` continua serializando requests de túnel (`{type:"request", id, method, path, headers, body}`) e refazendo-os em `http://localhost:8000{path}` para as rotas locais que não pertencem ao broker.

---

## SEÇÃO 3 — OAuth Providers (Integração do Agente)

A Vectora LTDA registra e mantém os OAuth Apps no Worker vectora-services.
O usuário final não cria app, não copia callback URL e nunca recebe client
secret. A tela de Integrações chama o endpoint local, que gera um state
criptograficamente aleatório e inicia o broker no Worker.

O Worker expõe:

- GET /oauth/integrations/providers — lista apenas os providers cujos secrets
  estão configurados no deploy;
- GET /oauth/integrations/{provider}/start — grava o state pendente com TTL de
  cinco minutos no Durable Object `OAUTH_RESULT` e redireciona para o provider;
- GET /oauth/integrations/{provider}/callback — recebe o callback público,
  valida o state e o provider, troca o code usando o secret da empresa e grava
  o resultado no Durable Object sem registrar credenciais nos logs;
- GET /oauth/integrations/{provider}/result/{state} — exige
  VECTORA_OAUTH_SECRET, retorna o resultado uma única vez e o apaga do Durable Object.

O redirect de retorno recebido do backend é aceito somente em
https://*.vectora.chat. O retorno contém apenas state; o token nunca é colocado
em URL, no binário desktop ou no comentário de log. O backend faz polling com
pequenos retries, associa o token
ao usuário que iniciou o fluxo e então o salva como override. Instalações sem
VECTORA_OAUTH_BROKER_URL exibem erro explícito e continuam podendo usar PAT/API
key manual quando o provider suportar esse modo.

Os secrets do Worker são configurados fora do repositório:

- VECTORA_OAUTH_SECRET;
- GITHUB_OAUTH_CLIENT_ID e GITHUB_OAUTH_CLIENT_SECRET;
- GITLAB_OAUTH_CLIENT_ID e GITLAB_OAUTH_CLIENT_SECRET;
- GOOGLE_OAUTH_CLIENT_ID e GOOGLE_OAUTH_CLIENT_SECRET;

## SEÇÃO 4 — Webhooks

> Webhooks de terceiros são configurados para apontar pra
> `https://{token}.vectora.chat/webhook/{provider}` (subdomínio da
> instalação — mesmo mecanismo de proxy da Seção 2.2). O Worker não
> interpreta o payload; só encaminha bytes pro backend local via
> WebSocket, onde `backend/api/handlers/webhooks.py::POST /webhook/{provider}`
> verifica a assinatura própria de cada provider (`X-Hub-Signature-256`
> pro GitHub, `X-Gitlab-Token`, `X-Slack-Signature`, `X-Linear-Signature`,
> `svix-signature` pro Resend) antes de processar.
>
> **Quem configura:** Você (Bruno) como desenvolvedor, uma única vez nos painéis dos providers.
> Usuários finais não mexem nisso.

---

### 4.1 GitHub Webhooks (nível de organização)

**Onde:** github.com/organizations/vectora-company → Settings → Webhooks

```
Payload URL: https://gateway.vectora.chat/webhook/github
Content type: application/json
Secret: <valor de GITHUB_WEBHOOK_SECRET>
Events: Pull requests, Pushes, Issues, Comments
```

> Para webhooks de repositórios individuais de usuários, o backend
> cria via API GitHub em nome do usuário autenticado.

**Backend:**

```env
GITHUB_WEBHOOK_SECRET=<secret>
```

---

### 4.2 GitLab Webhooks

```
URL: https://gateway.vectora.chat/webhook/gitlab
Secret Token: <valor de GITLAB_WEBHOOK_SECRET>
Triggers: Push, Merge requests, Comments
```

**Backend:**

```env
GITLAB_WEBHOOK_SECRET=<token>
```

---

### 4.3 Slack Event Subscriptions

```
Request URL: https://gateway.vectora.chat/webhook/slack
```

> Slack faz challenge de verificação na hora do cadastro — o backend
> precisa responder com `{"challenge": "..."}` (já implementado em
> `webhooks.py`; o Worker só encaminha o request, não intercepta).

---

### 4.4 Linear Webhooks

```
URL: https://gateway.vectora.chat/webhook/linear
Events: Issue, Comment, Cycle, Project
```

**Backend:**

```env
LINEAR_WEBHOOK_SECRET=<secret gerado pelo Linear>
```

---

### 4.5 Resend Webhooks

```
URL: https://gateway.vectora.chat/webhook/resend
Events: email.sent, email.delivered, email.bounced, email.complained
```

**Backend:**

```env
RESEND_WEBHOOK_SECRET=<signing secret Svix>
```

---

## SEÇÃO 5 — `services` (auth/billing/license/GDPR — antiga "Supabase")

> A company (`services.vectora.company`) **não usa Supabase** — todo o
> billing/auth/licenciamento roda no mesmo Worker `vectora-services`,
> persistido em D1 (`vectora-db`, binding `DB`). Sem RLS de banco:
> autorização é código, verificada em cada handler a partir da sessão
> resolvida pelo token Bearer.

### 5.1 Auth (`services/src/auth/`)

- **Sessão**: token opaco de 32 bytes (não JWT — a company é a única
  consumidora, comunicação server-to-server, sem motivo pra carregar
  claims), hash SHA-256 armazenado em D1, TTL 30 dias.
- **Signup**: exige Turnstile (`turnstileToken`), senha mínima 8
  caracteres, hash via PBKDF2-SHA256/WebCrypto nativo (100.000 iterações
  — teto real do runtime `workerd`, não bcrypt/argon2, que não rodam
  nele). Cria user + `VECTORA_TOKEN` (licença, 32 bytes hex) +
  subscription `free` automaticamente. Verificação de email obrigatória
  via fila `EMAIL_QUEUE`/Resend.
- **Login**: email/senha ou magic link.

### 5.2 Billing (`services/src/billing/`)

Dois provedores conforme o país do usuário (`country: "BR"|"INTL"` na
subscription, decidido no signup): **Stripe** para clientes
internacionais, **Asaas** para o Brasil (evita fricções do Stripe com
cartão/boleto BR). `POST /billing/checkout` decide o provider por
`sub.currency === "BRL"`. Webhooks de ambos (`POST /billing/webhooks?provider=stripe|asaas`)
mantêm `subscriptions.status`/`tier` sincronizados; cancelamento sempre
rebaixa pra `free` (o gate `require_pro()` no backend Python
verifica **tier**, não status). Também cobre cupons e presentes.

### 5.3 License (`services/src/license/`)

| Endpoint                                                                 | Auth                              | Propósito                                                                    |
| ------------------------------------------------------------------------ | --------------------------------- | ---------------------------------------------------------------------------- |
| `POST /license/validate`                                                 | Público, rate-limited (30/min IP) | Valida `VECTORA_TOKEN` — `{valid, tier, status, days_remaining, expires_at}` |
| `POST /license/agent-login`                                              | Email + senha                     | Devolve o `VECTORA_TOKEN` recuperável (não rotaciona a cada login)           |
| `POST /license/portal`                                                   | Sessão                            | Portal de pagamento (Stripe/Asaas)                                           |
| `POST /license/rotate`                                                   | Sessão                            | Rotaciona o token manualmente                                                |
| `GET /license/token-status`, `/license/token/reveal`, `/license/history` | Sessão                            | Consulta/histórico                                                           |

### 5.4 GDPR (`services/src/gdpr/`)

`POST /gdpr/export` junta `users`+`subscriptions`+`license_checks`+`api_keys`,
grava em R2 (`exports/{userId}-{ts}.json`), serve via `GET /gdpr/export/*`
com checagem de dono. `POST /gdpr/delete` faz soft-delete + revoga sessão

- email; o cron diário (03:00 UTC) enfileira hard-delete (fila
  `vectora-jobs`) pra contas expiradas há 30+ dias — cancela Stripe/Asaas e
  `DELETE FROM users` (cascade cuida do resto).

---

## SEÇÃO 6 — Hospedagem (Vercel + Cloudflare)

`vectora.company` (o **site**, marketing/dashboard, TanStack Start) e
`services.vectora.company`/`gateway.vectora.chat` (a **API**, Worker
`vectora-services`) são sistemas de hospedagem completamente diferentes,
apesar de compartilharem domínio raiz.

### 6.1 Variáveis de Ambiente no Projeto Vercel `vectora-company` (o site)

| Var                             | Valor                                         | Ambiente                                                                                                  |
| ------------------------------- | --------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `VITE_GA4_MEASUREMENT_ID`       | `G-K0JK9B2YH2`                                | Production                                                                                                |
| `VITE_GOOGLE_SITE_VERIFICATION` | `i9Af68Fzq4E9N6QEmMHxz8Bp5xpTZXzPyNqG5IeoZbo` | Production                                                                                                |
| `TURNSTILE_SECRET_KEY`          | `<secret key>`                                | Production                                                                                                |
| `VITE_TURNSTILE_SITE_KEY`       | `<site key>`                                  | Production                                                                                                |
| `VITE_SERVICES_URL`/equivalente | `https://services.vectora.company`            | Production — aponta o site pra API real, checar nome exato da env var em `company/` na hora de configurar |

> As vars `SUPABASE_SERVICE_ROLE_KEY`/`VITE_SUPABASE_URL`/`VITE_SUPABASE_KEY`
> da versão anterior deste doc **não existem mais** — não há Supabase.

### 6.2 Docs — Projeto `vectora-docs` (Vercel)

- Install command: `npx --yes pnpm@11 install` (via `docs/vercel.json`)
- Build command: `pnpm build`
- Env vars: `DOCS_URL`, `APP_URL` (ver `docs/.env.example`)

---

## SEÇÃO 7 — Verificação

### 7.1 Gateway online

```powershell
curl https://gateway.vectora.chat/health
# Esperado: 200 OK
```

### 7.2 Wildcard DNS

```powershell
curl https://abc123.vectora.chat/
# Esperado: gateway responde (4xx sem sessão ativa — normal)
```

### 7.3 OAuth device flow (licença)

```powershell
# 1. Backend: POST /license/oauth/init → {state, auth_url}
# 2. Abrir auth_url → logar na company → autorizar
# 3. Backend: GET /license/oauth/poll?state=... → {ok: true}
```

### 7.4 Integração GitHub (agente)

```powershell
# No app Vectora: Configurações → Integrações → GitHub → Conectar
# Redireciona para o GitHub via {token}.vectora.chat, autentica, volta ao app
# Agente consegue clonar repos, criar PRs, etc.
```

### 7.5 Services (auth/billing/license/gdpr)

```powershell
curl https://services.vectora.company/license/validate -X POST -d '{"token":"..."}'
# Esperado: {"valid": true/false, "tier": "free"|"pro", ...}
```

---

## SEÇÃO 8 — Ordem de Execução

```
[ ] 1. Cloudflare: zona vectora.chat com a rota wildcard *.vectora.chat/* → vectora-services
[ ] 2. Worker: wrangler secret put GATEWAY_HMAC_SECRET
[ ] 2a. Worker: wrangler secret put GATEWAY_INTERNAL_SECRET
[ ] 3. Worker: wrangler secret put VECTORA_OAUTH_SECRET
[ ] 4. Worker: wrangler secret put VECTORA_APP_SECRET
[ ] 5. Worker: wrangler secret put STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET / STRIPE_PRICE_PRO_USD
[ ] 6. Worker: wrangler secret put ASAAS_API_KEY / ASAAS_API_URL
[ ] 6a. Worker: wrangler secret put ASAAS_WEBHOOK_SECRET
[ ] 7. Worker: wrangler secret put RESEND_API_KEY / TURNSTILE_SECRET_KEY
[ ] 8. Worker: aplicar migrations D1 (services/migrations/)
[ ] 9. Worker: wrangler deploy
[ ] 9a. Worker: configurar GHA_BOT_ENCRYPTION_KEY
[ ] 10. Cloudflare: configurar Custom Domain services.vectora.company → vectora-services (fora do wrangler.toml, validar mecanismo com quem administra o DNS)
[ ] 11. Backend: carregar VECTORA_OAUTH_BROKER_URL=https://services.vectora.company e adicionar VECTORA_APP_SECRET/VECTORA_OAUTH_SECRET ao defaults.env
[ ] 12. Testar: GET /gateway/status no backend → ver subdomínio
   [ ] 13. Worker: configurar `GITHUB_OAUTH_CLIENT_ID` e `GITHUB_OAUTH_CLIENT_SECRET`
   [ ] 14. Worker: configurar `GOOGLE_OAUTH_CLIENT_ID` e `GOOGLE_OAUTH_CLIENT_SECRET`
   [ ] 15. Worker: configurar `GITLAB_OAUTH_CLIENT_ID` e `GITLAB_OAUTH_CLIENT_SECRET`
   [ ] 16. Slack: configurar `SLACK_BOT_TOKEN` e `SLACK_APP_TOKEN` no fluxo local Socket Mode
   [ ] 17. Vercel: adicionar env vars no projeto do site (company)
   [ ] 18. Testar fluxo OAuth GitHub end-to-end (via app Vectora)
   [ ] 19. Testar license device flow
   [ ] 20. Testar signup/login/billing (Stripe sandbox + Asaas sandbox)
```

---

## SEÇÃO 9 — Secrets Consolidados

### Worker `vectora-services` (Cloudflare wrangler secrets)

```
GATEWAY_HMAC_SECRET     → interno ao gateway, gera tokens estáveis por instalação
GATEWAY_INTERNAL_SECRET → autentica chamadas internas do Worker ao gateway
VECTORA_OAUTH_SECRET    → compartilhado com company e backend (polling one-shot)
GITHUB_OAUTH_CLIENT_ID / GITHUB_OAUTH_CLIENT_SECRET → secrets do OAuth App da Vectora LTDA
GITLAB_OAUTH_CLIENT_ID / GITLAB_OAUTH_CLIENT_SECRET → secrets do OAuth App da Vectora LTDA
GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET → secrets do OAuth App da Vectora LTDA
VECTORA_APP_SECRET      → prova que cliente é Vectora legítimo (fixo por produto)
STRIPE_SECRET_KEY       → billing internacional
STRIPE_WEBHOOK_SECRET   → valida webhooks do Stripe
STRIPE_PRICE_PRO_USD    → price id do plano Pro
ASAAS_API_KEY           → billing Brasil
ASAAS_API_URL           → endpoint da API Asaas (sandbox vs produção)
ASAAS_WEBHOOK_SECRET    → valida webhooks do Asaas
GHA_BOT_ENCRYPTION_KEY  → cifra credenciais do bot GHA
RESEND_API_KEY          → envio de email (verificação, notificações)
TURNSTILE_SECRET_KEY    → anti-bot no signup
```

### Backend (`~/.vectora/.env`)

```env
VECTORA_APP_SECRET=<mesmo do Worker>
VECTORA_OAUTH_SECRET=<mesmo do Worker>
VECTORA_OAUTH_BROKER_URL=https://services.vectora.company
GATEWAY_URL=wss://gateway.vectora.chat
GATEWAY_ENABLED=true

# Webhook Secrets
GITHUB_WEBHOOK_SECRET=
GITLAB_WEBHOOK_SECRET=
SLACK_SIGNING_SECRET=
LINEAR_WEBHOOK_SECRET=
RESEND_WEBHOOK_SECRET=
```

### Vercel (projeto do site `vectora.company`)

```
TURNSTILE_SECRET_KEY=<mesmo do Worker, se o form de signup roda no site>
VITE_TURNSTILE_SITE_KEY=<site key>
```

---

## SEÇÃO 10 — Tool Gateway (roadmap, design apenas)

> Escopo desta seção: arquitetura decidida + pontos de extensão identificados
> no código real. **Não é implementação** — fica para uma sprint futura
> dedicada, quando o volume de tools cobertas justificar o investimento.
> Distinto da Seção 3 (OAuth de integração do agente): a Seção 3 já entrega
> o mecanismo genérico gateway↔backend; o Tool Gateway é uma camada em cima
> disso que resolve um problema de **fricção de setup**, não de mecanismo.

### O problema hoje

Cada tool de integração externa (`backend/tools/slack.py`, `gdrive.py`,
`gmail.py`, `jira.py`, `linear.py`, `notion.py`, `gh.py`) lê sua **própria**
env var (`SLACK_BOT_TOKEN`, `GITHUB_PERSONAL_ACCESS_TOKEN`, etc. — ver
`slack.py::_token()`). Os OAuth Apps centralizados são registrados uma única
vez pela Vectora LTDA no Worker; o usuário final apenas autoriza a conta. Isso
remove a fricção de configuração duplicada:

1. **Pro operador do Vectora** (Bruno): N providers = N OAuth Apps pra
   manter, N conjuntos de client_id/client_secret, N callbacks.
2. **Pro usuário final**: cada integração nova é um fluxo de conexão
   separado em Integrações, mesmo que várias tools compartilhem o mesmo
   provider (ex.: Slack `slack_send`/`slack_list_channels`/`slack_read` já
   compartilham `SLACK_BOT_TOKEN` — isso já funciona bem; o problema é
   entre _providers diferentes_, não dentro do mesmo).

### O que o Tool Gateway resolve — e o que não resolve

**Resolve**: um usuário pode conectar GitHub/Google/GitLab através dos OAuth
Apps operados pela Vectora (client_id/secret do Worker), reduzindo o fluxo a
"clicar em Conectar". Slack continua no Socket Mode local porque seu contrato
exige os tokens de bot e de app. O catálogo centraliza apenas os providers
efetivamente suportados pelo broker.

**Não resolve, e não deve**: substituir BYOK. O princípio fundacional do
produto (`market-and-positioning.md`) é que o usuário sempre pode trazer
suas próprias credenciais — o Tool Gateway é uma opção adicional de setup
mais rápido, nunca a única via. Qualquer tool continua funcionando com a
env var direta (`SLACK_BOT_TOKEN` etc.) configurada manualmente em
Integrações, exatamente como hoje — o gateway só populariza essa mesma env
var por um caminho alternativo (ver fluxo abaixo).

### Arquitetura decidida

Reaproveita a primitiva que a Seção 3 já entrega — o Worker recebe callbacks
OAuth e guarda o resultado associado ao `state` em um Durable Object de uso
único. O backend local faz polling autenticado e consome o resultado uma única
vez; nenhum token é encaminhado pelo WebSocket. O Tool Gateway generaliza isso
de "OAuth por provider configurado individualmente" pra "catálogo de providers
com credenciais operadas pela Vectora, resultado sempre pousando na mesma env
var que a tool já lê":

```text
Usuário clica "Conectar" no catálogo de integrações (aba Integrações,
já teria uma seção "via Vectora" ao lado de "BYOK")
        │
        ▼
services.vectora.company/oauth/integrations/{provider}/start
   (usa client_id/secret OPERADOS PELA VECTORA — Seção 3, já registrados
    uma vez por Bruno, reaproveitados por todos os usuários finais)
        │
        ▼
OAuth callback → services.vectora.company/oauth/integrations/{provider}/callback
        │
        ▼
OAuthResult (Durable Object) grava o resultado com TTL físico de cinco minutos
e uso único associado ao state
        │
        ▼
Backend faz polling autenticado e consome o resultado uma vez; então grava o
token na MESMA env var que a tool já lê hoje
(GITHUB_PERSONAL_ACCESS_TOKEN, GITLAB_PERSONAL_ACCESS_TOKEN, ...) via
POST /auth/envs (mesmo mecanismo da aba Integrações) — as tools em
backend/tools/*.py não mudam NADA, continuam lendo a env var de sempre
```

**Ponto de extensão central**: nenhuma tool precisa saber se o token veio
via Tool Gateway ou via BYOK manual — ambos os caminhos convergem na mesma
env var, gravada pelo mesmo `POST /auth/envs`. Isso significa que o Tool
Gateway é **puramente aditivo** na aba Integrações (mais uma forma de
popular a env var), sem exigir refactor de nenhuma tool existente.

### Escopo de providers (fase 1, ao implementar)

Providers que já têm tool própria e leem env var/override isolado —
candidatos naturais por já terem o "outro lado" pronto: GitHub
(`GITHUB_PERSONAL_ACCESS_TOKEN`, já cobre
`gh.py`), Google (`gmail.py`/`gdrive.py`, já lêem
`GOOGLE_ACCESS_TOKEN`/`GOOGLE_REFRESH_TOKEN` gravados pelo fluxo OAuth
descrito na Seção 3.2 — candidato mais próximo de já "funcionar" com o Tool
Gateway, resta só trocar client_id/secret operados por usuário pelos
operados pela Vectora), Linear, Jira, Notion. Não inclui MCP marketplace
(já resolvido via `POST /auth/envs` no fluxo de instalação, sem precisar do
Tool Gateway).

### Testes (quando implementado — fora de escopo desta seção)

- Conectar via Tool Gateway grava a mesma env var que a tool já lê — tool
  funciona sem nenhuma mudança de código.
- BYOK continua funcionando em paralelo — desconectar do Tool Gateway não
  apaga uma env var configurada manualmente por cima (BYOK sempre vence,
  nunca é sobrescrito silenciosamente por uma conexão via gateway mais
  antiga).
- Erro/borda: provider sem OAuth App operado pela Vectora ainda configurado
  (client_id vazio) — catálogo mostra a opção "via Vectora" desabilitada
  com mensagem clara, não um botão que falha silenciosamente ao clicar.

### Verificação (quando implementado)

Conectar GitHub via Tool Gateway, confirmar que `github_*` funciona sem
nenhuma mudança de código na tool; depois configurar o Slack localmente com
`SLACK_BOT_TOKEN` e `SLACK_APP_TOKEN` e confirmar que `slack_send` funciona
sem nenhuma mudança de código na tool; depois configurar `SLACK_BOT_TOKEN`
manualmente por cima e
confirmar que o valor manual vence (BYOK sempre tem prioridade).

# vectora.company

Site institucional + dashboard de billing/licença da Vectora — a SPA/SSR que roda em `vectora.company`. Landing pública (marketing, pricing, FAQ, legal) e um dashboard autenticado (token, assinatura, API keys, conta) para clientes Pro.

**Este app nunca fala com bancos de dados diretamente.** Toda autenticação, billing e licenciamento é feito via chamadas HTTP server-to-server para o Worker Cloudflare `services` (`services.vectora.company`) — este projeto não tem Supabase, não tem Postgres próprio, e não tem service role key nenhuma. Ver `services/README.md` para a arquitetura do backend.

---

## Stack

| Camada          | Tecnologia                                                                                                                                                                            |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Framework       | [TanStack Start](https://tanstack.com/start) (SSR via Nitro)                                                                                                                          |
| Roteamento      | TanStack Router (file-based, type-safe) — `src/routes/`                                                                                                                               |
| Data fetching   | TanStack Query                                                                                                                                                                        |
| React           | React 19                                                                                                                                                                              |
| Estilos         | Tailwind CSS 4 + Radix UI                                                                                                                                                             |
| Backend         | Server functions (`createServerFn`) chamando o Worker `services` via `fetch` — nunca DB direto                                                                                        |
| Sessão          | Cookie `vsession` HttpOnly (token opaco emitido pelo `services`, não JWT)                                                                                                             |
| Validação       | Zod (schemas dos server functions)                                                                                                                                                    |
| Toasts          | `sonner`                                                                                                                                                                              |
| i18n            | [Paraglide JS](https://inlang.com/m/gerre34r/library-inlang-paraglideJs) — 7 idiomas (`pt` padrão, `en`, `es`, `fr`, `it`, `de`, `ru`), compile-time, `m()` de `#/paraglide/messages` |
| Observabilidade | Sentry (`@sentry/tanstackstart-react`)                                                                                                                                                |
| Analytics       | Plausible (self-hosted, sem cookies) + GA4                                                                                                                                            |
| Bot protection  | Cloudflare Turnstile                                                                                                                                                                  |
| Deploy          | Vercel (Nitro preset `vercel`)                                                                                                                                                        |
| Package manager | pnpm                                                                                                                                                                                  |
| Testes          | Vitest + Testing Library                                                                                                                                                              |
| Qualidade       | TypeScript strict + ESLint + Prettier                                                                                                                                                 |

> **Nota histórica:** versões antigas deste README descreviam Supabase Auth/Postgres/Edge Functions e Bun como package manager — essa arquitetura foi substituída pelo Worker `services` (Cloudflare, D1) e por pnpm. Este arquivo reflete o código atual, não o plano original.

---

## Arquitetura

### Server functions → `services`

Nenhuma lógica de negócio mora neste projeto. Cada arquivo em `src/server/fns/` é uma casca fina (`createServerFn` + validação Zod) que chama o Worker `services`:

| Arquivo                      | Cobre                                                                       |
| ---------------------------- | --------------------------------------------------------------------------- |
| `server/fns/auth.ts`         | `getSession`, `signUp`, `signIn`, `signOut`, `verifyEmail`, `sendMagicLink` |
| `server/fns/subscription.ts` | `getSubscription`, `createCheckout`, `createPortal`, `getLicenseHistory`    |
| `server/fns/token.ts`        | `getTokenStatus`, `getToken` (recuperável), `rotateToken`                   |
| `server/fns/api-keys.ts`     | `listApiKeys`, `createApiKey`, `revokeApiKey`                               |
| `server/fns/gdpr.ts`         | `exportData`, `requestAccountDeletion`                                      |
| `server/fns/issues.ts`       | `submitIssue`, `listOpenIssues`, `joinWaitlist`                             |
| `server/fns/profile.ts`      | `updateProfile`                                                             |
| `server/fns/oauth.ts`        | `authorizeDevice` (CLI/desktop device flow)                                 |

`src/lib/services/client.ts` centraliza a comunicação: `servicesFetch<T>()` injeta o Bearer do cookie `vsession`, define `Content-Type`, e normaliza erros (`body.error` ou fallback `services_error_{status}`). Sessão é lida/escrita via `getSessionToken`/`setSessionCookie`/`clearSessionCookie`, sempre em cookie HttpOnly + `Secure` + `SameSite=Lax` — nunca acessível a JavaScript do browser.

### Rotas (`src/routes/`)

| Rota                                             | O que é                                                             |
| ------------------------------------------------ | ------------------------------------------------------------------- |
| `/`                                              | Landing (hero, showcase, pricing, why-self-hosted)                  |
| `/login`, `/signup`                              | Auth (email/senha + magic link)                                     |
| `/downloads`                                     | Detecção de SO (`detectOS`) + links de instalador                   |
| `/pricing` _(via `#pricing` na landing)_         | Tabela de planos                                                    |
| `/faq`, `/support`, `/issues`                    | Suporte público                                                     |
| `/privacy`, `/terms`, `/cookies`, `/sla`, `/dpa` | Páginas legais                                                      |
| `/roadmap`                                       | Roadmap público                                                     |
| `/auth/device`, `/auth/verify`                   | Device flow (CLI) + confirmação de email/magic link                 |
| `/dashboard/*`                                   | Área autenticada — ver abaixo (auth guard em `dashboard/route.tsx`) |
| `/api/og`                                        | Gera a imagem Open Graph dinamicamente (Satori)                     |
| `/sitemap.xml`                                   | Sitemap gerado                                                      |

**Dashboard** (`src/routes/dashboard/`): `index` (revelar/rotacionar `VECTORA_TOKEN`), `license` (status da assinatura + histórico de validações), `billing` (checkout/portal Stripe/Asaas), `api-keys` (CRUD de chaves com escopos), `account` (perfil, magic link, export GDPR, exclusão de conta).

### Componentes

- **`components/landing/`** — 8 componentes, puro layout de marketing (Hero, ShowcaseGifs, PricingSection, WhySelfHosted, etc.) — sem lógica de negócio, não cobertos por teste unitário (nada a testar além de render estático).
- **`components/dashboard/`** — `TokenReveal`, `BillingSection`, `AccountSection`, `LicenseStatus`/`LicenseHistory`, `ApiKeysList` — cada um consome um hook de `src/hooks/` via TanStack Query e fala com os server functions acima.
- **`components/shared/`** — `Header`, `Footer`, `Logo`, `AuthLayout`, `PageHeader`, `FaqAccordion`, `CookieConsent`, `ThemeToggle`, `LocaleSwitcher`, `Turnstile`.

### Hooks (`src/hooks/`)

`use-session.ts`, `use-subscription.ts` (+ `useLicenseHistory`, `useCreateCheckout`, `useCreatePortal`), `use-api-keys.ts` (+ `useCreateApiKey`, `useRevokeApiKey`) — todos wrappers finos de `useQuery`/`useMutation` em cima dos server functions, com invalidação de cache nas mutations relevantes.

---

## Desenvolvimento

```bash
# Instalar dependências
pnpm install

# Configurar variáveis de ambiente
cp .env.example .env.local
# Preencher .env.local: SERVICES_URL, Sentry, Turnstile, GA4/Plausible

# Dev server
pnpm dev                 # http://localhost:3000

# Build de produção
pnpm build
pnpm preview
```

**Pré-requisitos**: Node 24+ · pnpm · uma instância do worker `services` acessível (local via `wrangler dev` ou apontando para `https://services.vectora.company`).

---

## Testes

```bash
pnpm test              # vitest run
pnpm typecheck          # paraglide compile + tsr generate + tsc --noEmit
pnpm lint               # eslint
```

Suíte atual: **28 arquivos de teste, 178 testes**, cobrindo toda a lógica de negócio testável do projeto — server functions (happy path + validação Zod + branches de erro), hooks (TanStack Query com mocks dos server functions), lib (`services/client`, `theme`, `analytics/ga4`, `analytics/plausible`) e componentes com lógica real (dashboard inteiro + shared não-estático). Segue o padrão de par caminho-feliz/caminho-de-erro no mesmo arquivo:

| Área                                  | Arquivos | O que cobre                                                                                                                                                                                                             |
| ------------------------------------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/server/fns/*.test.ts`            | 8        | Happy path, validação Zod (email/senha/enum/uuid inválidos), branches (`not_found`, token recuperável, tolerância a erro no logout)                                                                                     |
| `src/hooks/*.test.tsx`                | 3        | `useQuery`/`useMutation` com invalidação de cache, redirect via `window.location`                                                                                                                                       |
| `src/lib/**/*.test.ts`                | 4        | Cookie de sessão, fetch tipado + erros, tema claro/escuro + `matchMedia`, analytics com/sem `window.gtag`/`window.plausible`                                                                                            |
| `src/components/shared/*.test.tsx`    | 7        | CookieConsent, ThemeToggle, Header (menu mobile + dropdown de idioma), Logo, PageHeader, FaqAccordion, AuthLayout                                                                                                       |
| `src/components/dashboard/*.test.tsx` | 5        | TokenReveal (recuperável + rotate + copy), BillingSection (BR/INTL, free/pro), AccountSection (form + GDPR + delete), LicenseStatus/History (status desconhecido → fallback, IP mascarado), ApiKeysList (criar/revogar) |
| `src/routes/downloads.test.ts`        | 1        | Detecção de SO a partir do `userAgent`                                                                                                                                                                                  |

Padrões usados nos mocks (ver `vitest.setup.ts`):

- `createServerFn` (`@tanstack/react-start`) é mockado globalmente para rodar a validação Zod de verdade e chamar o handler direto — sem precisar de um servidor Nitro real.
- Testes de server functions mockam `#/lib/services/client` inteiro (isolando a lógica de negócio do fetch real).
- Testes de componente mockam `#/paraglide/messages` (Proxy que devolve a chave como string) e `@tanstack/react-router`'s `Link` (renderiza como `<a>`).
- Testes de hook/componente com dados usam `QueryClientProvider` real (não mockado) para exercitar cache/invalidação de verdade.

---

## Variáveis de ambiente

Ver `.env.example` para a lista completa. As mais relevantes:

```env
# services (Cloudflare Worker — auth/billing/license/GDPR/api-keys). Server-only.
SERVICES_URL=https://services.vectora.company

# Turnstile (proteção anti-bot em signup/login/issues) — chave pública, client-safe.
VITE_TURNSTILE_SITE_KEY=

# Sentry
VITE_SENTRY_DSN=
SENTRY_ORG=vectora
SENTRY_PROJECT=vectora-company
SENTRY_AUTH_TOKEN=

# Analytics
VITE_GA4_MEASUREMENT_ID=
VITE_PLAUSIBLE_DOMAIN=vectora.company

# 'waitlist' esconde o produto e mostra só o CTA de lista de espera na landing.
VITE_LAUNCH_MODE=
```

---

## Deploy

Vercel, preset Nitro `vercel` (`vercel.json`). Headers de segurança (`X-Frame-Options`, `Strict-Transport-Security`, etc.) já configurados no `vercel.json`. Em dev local sem Vercel, usar `NITRO_PRESET=node-server`.

---

## Licença

Software proprietário — código fechado, parte do produto Vectora.

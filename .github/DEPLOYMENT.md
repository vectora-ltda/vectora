# Deployment & GitHub Actions

Pipeline definido em [`workflows/vectora.yml`](workflows/vectora.yml). O setup
de Python/uv é centralizado na composite action
[`actions/setup-uv`](actions/setup-uv/action.yml). A borda web/edge do
monorepo (`company/`, `docs/`, `services/`) tem ciclo próprio em
[`workflows/edge.yml`](workflows/edge.yml) — não coberto por este documento.

## Visão geral do pipeline (`vectora.yml`)

Disparado por `push` em `master`/`release/**`, por `pull_request` contra
qualquer branch — inclusive PRs empilhados em branches de feature — (ambos
filtrados por path: `vectora/**`, `utils/**`, este workflow e
`.github/actions/**`), ou manualmente via `workflow_dispatch`. Os jobs de
publicação seguem restritos a `refs/heads/master`. Os jobs 1-5
rodam em qualquer disparo dentro do path filtrado; 6 (integration/e2e) só em
`master`/tags; 7-9 (release nativo + publicação) só em tag `v*` ou
`workflow_dispatch`. Em ordem:

1. **Lint & Format Check** — `ruff check`/`ruff format --check` + `ty check` (Python).
2. **Security Scanning** — `bandit` + `pip-audit` (best-effort, não bloqueia).
3. **Frontend (lint + types + tests)** — `oxlint` + `tsc --noEmit` + `vitest`.
4. **Build Verification** — `compileall backend` + build da SPA (Vite).
5. **Unit Tests** — `pytest` (marker `not live`, isola testes reais de LanceDB num step à parte).
6. **Integration + E2E Tests** — só em `master` e tags `v*` (precisam de chaves reais).
7. **Release Native** — só em tag `v*` ou `workflow_dispatch`: Nuitka (`backend.pyd`),
   PyInstaller e instaladores Electron (Win/macOS/Linux) via matrix.
8. **Publish to update channel (R2 + KV)** — sobe os instaladores pro
   Cloudflare R2 e atualiza o ponteiro de versão no KV (canal `latest`),
   consumido pelo `electron-updater` do app instalado.
9. **Publish gha-bot CLI binary (R2)** — só linux/x64: publica o binário
   headless usado pela Vectora Bot Action.

> Distribuição: **instaladores nativos** (Nuitka + Electron, Win/macOS/Linux),
> publicados no canal de update (Cloudflare R2 + KV) consumido pelo
> `services/` (gateway + updates unificados, era `relay/` + `update-server/`).
> **Não existe imagem Docker do app publicada em nenhum registry** — o
> `docker-compose.yml` da raiz de `vectora/` sobe só a infra de
> desenvolvimento (PostgreSQL + Redis + Qdrant, modo `STORAGE_MODE=complete`);
> o backend em si roda no host, nunca como container (ver `CLAUDE.md`).

A versão de Python é fixada explicitamente no workflow — não segue a mais
recente disponível automaticamente, por causa da restrição do Nuitka abaixo.

## Python 3.13 (não 3.14, por ora)

`PYTHON_VERSION: "3.13"` no workflow casa com o `requires-python` do projeto,
com o `.python-version` da raiz e com a CPython que o `uv` resolve localmente.
O Nuitka 4.1.x (usado no `release-native`) **só suporta oficialmente até 3.13**
— 3.14 vira oficial no Nuitka 4.2. Subir antes disso quebra o build nativo.

## Secrets necessários

Configurados em **Settings → Secrets and variables → Actions**.

| Secret                                                       | Usado em                                    | Descrição                                                       |
| ------------------------------------------------------------ | ------------------------------------------- | --------------------------------------------------------------- |
| `GOOGLE_API_KEY` / `COHERE_API_KEY` / `TAVILY_API_KEY`       | test-unit, test-integration                 | Chaves para testes (fallback `test-key`/vazio conforme o job).  |
| `LLM_PROVIDER` / `LOG_LEVEL`                                 | test-unit, test-integration                 | Config dos testes (defaults: `google-genai` / `INFO`).          |
| `WIN_CERTIFICATE_BASE64` / `WIN_CERTIFICATE_PASSWORD`        | release-native                              | Assinatura Windows (opcional — sem isso, build não assinado).   |
| `APPLE_ID` / `APPLE_APP_SPECIFIC_PASSWORD` / `APPLE_TEAM_ID` | release-native                              | Notarização macOS (opcional).                                   |
| `VECTORA_RELEASES_TOKEN`                                     | release-native                              | Token p/ publicar instaladores nas releases privadas do GitHub. |
| `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID`             | publish-update-channel, publish-gha-bot-cli | Auth Cloudflare (KV/Workers).                                   |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`                  | publish-update-channel, publish-gha-bot-cli | Upload S3-compatível pro bucket R2.                             |

Sem imagem Docker publicada, o rollback do app acontece pelo canal de
update dos instaladores nativos, não por reapontar um `docker-compose.yml`.

## Rollback

O canal de update (`services/scripts/release.ts`) mantém `previous_stable` e
uma lista de quarentena por versão — uma versão problemática pode ser
colocada em quarentena via esse script, fazendo o `electron-updater` de
instalações existentes caírem de volta pra `previous_stable` automaticamente
(ver `services/src/updates/worker.ts::resolveVersion`). Para uma reversão
manual completa, republique a tag/release anterior disparando `release-native`
e `publish-update-channel` via `workflow_dispatch` a partir do commit
correspondente.

---

**Removido nesta revisão:** menções a jobs de Docker Build/Push (GHCR) e
deploy via SSH/VPS que nunca chegaram a existir neste pipeline — a
distribuição sempre foi via instaladores nativos + canal de update, nunca
imagem Docker publicada.

<!-- markdownlint-disable MD033 MD041 -->

# Vectora — Monorepo

Monorepo da Vectora. Cada produto vive em sua própria pasta, com toolchain e
lockfile independentes. O que conecta tudo é o **GitHub Actions** (um workflow
por projeto, gated por path) e o **pre-commit** compartilhado na raiz.

## Estrutura

| Pasta                             | Projeto                                                                                                                           | Stack                            | Deploy                                                       |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | -------------------------------- | ------------------------------------------------------------ |
| [`vectora/`](vectora/README.md)   | App Vectora (assistente de IA self-hosted)                                                                                        | Python (uv) + Vite + Electron    | Instaladores nativos (Nuitka + Electron) via canal de update |
| [`company/`](company/README.md)   | Site institucional                                                                                                                | TanStack Start + Vite (pnpm)     | Vercel → **vectora.company**                                 |
| [`docs/`](docs/README.md)         | Documentação                                                                                                                      | Hugo                             | Vercel → **docs.vectora.company**                            |
| [`services/`](services/README.md) | Gateway (OAuth/webhooks pro desktop, ex-relay) + updates (distribuição de releases) — era `relay/` + `update-server/`, unificados | Hono + Cloudflare Workers (pnpm) | Cloudflare (wrangler)                                        |

> O app em si (backend Python, frontend Vite, casca Electron) fica todo dentro
> de `vectora/` — veja [vectora/README.md](vectora/README.md) para detalhes de
> arquitetura, build e CLI.

## CI/CD (`.github/workflows/`)

4 workflows reais, cada um **gated por path** (`on.push/pull_request.paths`):
uma mudança só no `vectora/` não dispara o build/deploy de `company`/`docs`/`services`.

| Workflow             | Dispara em                             | Faz                                                                                                                                 |
| -------------------- | -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `vectora.yml`        | `vectora/**`, tags `v*`                | lint • security • build • testes (unit/integration/e2e) • release nativo (Nuitka + Electron) • publica no canal de update (R2 + KV) |
| `edge.yml`           | `company/**`, `services/**`, `docs/**` | lint/typecheck/test dos 3 projetos • deploy automático em push em master (Vercel + Cloudflare)                                      |
| `release-please.yml` | push em master                         | mantém o PR de release acumulado (Conventional Commits) — mesclar publica a tag `vX.Y.Z`                                            |
| `pr-checks.yml`      | pull requests                          | labels automáticos • valida título em Conventional Commits                                                                          |

Não existe workflow de Docker Build/Push, CodeQL nem triage de issues — a
distribuição do app vectora é via instaladores nativos (não imagem Docker).

### Secrets esperados

| Secret                                                                                                                                     | Usado por                                      |
| ------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------- |
| `GOOGLE_API_KEY`, `COHERE_API_KEY`, `TAVILY_API_KEY`, `LLM_PROVIDER`, `LOG_LEVEL`                                                          | testes do vectora                              |
| `WIN_CERTIFICATE_BASE64`, `WIN_CERTIFICATE_PASSWORD`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`, `VECTORA_RELEASES_TOKEN` | assinatura/publicação dos instaladores nativos |
| `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID_COMPANY`, `VERCEL_PROJECT_ID_DOCS`                                                     | deploy Vercel (company + docs)                 |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`                                                | deploy do services + canal de update (R2)      |

## Desenvolvimento

Cada projeto é independente — entre na pasta e use o gerenciador dele:

```bash
# App Vectora (Python + Vite + Electron)
cd vectora && uv sync && uv run vectora start

# Site institucional
cd company && pnpm install && pnpm dev

# Docs (Hugo)
cd docs && hugo server

# Services (gateway + updates)
cd services && pnpm install && pnpm dev
```

### Pre-commit (raiz)

O `.pre-commit-config.yaml` na raiz cobre todos os projetos (ruff/ty/bandit no
`vectora/`, prettier/oxlint/tsc no frontend, actionlint nos workflows):

```bash
pre-commit install
pre-commit run --all-files
```

## Licença

Proprietária. Consulte [LICENSE](./LICENSE).

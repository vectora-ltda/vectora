# Testes E2E (Playwright)

Testes de browser real do chat do Vectora. Cobrem os fluxos que só fazem
sentido com a stack inteira de pé:

- **`streaming.spec.ts`** — a resposta do assistente é renderizada
  **incrementalmente** (token a token), não de uma vez no final. Amostra o
  comprimento do texto em alta frequência e exige vários comprimentos
  crescentes distintos (prova de streaming vs. buffering).
- **`session-recovery.spec.ts`** — o histórico **sobrevive ao reload** da
  página (zera o cache em memória → força `getHistory` ao backend); e o
  **título** da sessão é atribuído pela IA (não a cópia do prompt).
- **`workbench-tabs.spec.ts`** — troca de aba do workbench (regressão do bug
  real em que a troca de aba travava o conteúdo na primeira aba montada
  enquanto só o header seguia trocando).
- **`git-workflow.spec.ts`** — cria workspace real, edita/cria arquivo pela
  aba Arquivos, vê a mudança na aba Git, stage + commit reais pela UI.
- **`web-search-tool.spec.ts`** — prompt que força a tool `web_search` real
  (Tavily, sem mock) e confirma que a resposta referencia o resultado.
- **`settings-tabs.spec.ts`** — dialog Ambiente (Integrações / Provider
  Routing): render das abas + interação básica, sem persistir credenciais.
- **`plan-mode.spec.ts`** — prompt que pede um plano gera tarefas reais
  (`write_todos`) visíveis na aba Plano.

## Pré-requisitos

1. **Backend rodando** em `http://127.0.0.1:8080` com uma chave de provider
   configurada (ex.: `GOOGLE_API_KEY`) — os testes exercitam o LLM real.
2. **Browser de teste do Playwright** instalado (uma vez). Isso é usado apenas
   pela suíte E2E e não é o Chromium do app distribuído; em produção o Vectora
   usa o Chromium que vem dentro do Electron:
   ```
   pnpm --dir frontend exec playwright install chromium
   ```
3. **Credenciais e2e** (opcional; há defaults). Numa instalação limpa (sem
   usuários), o `global-setup` cria o root automaticamente:
   ```
   E2E_EMAIL=e2e@vectora.local
   E2E_PASSWORD=Vectora-e2e-2026!
   ```
   Num backend que já tem usuários, defina-as batendo com um usuário válido.

## Rodar

```
pnpm --dir frontend test:e2e        # headless
pnpm --dir frontend test:e2e:ui     # modo UI (debug)
```

O dev server do Vite (porta 5173) sobe automaticamente (`webServer` na
`playwright.config.ts`) e faz proxy de `/auth` e `/vectora` para o backend.

> Estes testes **não** entram no `scons tests` (exigem browser instalado +
> backend + LLM real). Rode-os sob demanda no fluxo de QA.

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

1. **Vectora desktop de desenvolvimento rodando** com o renderer atualizado,
   Electron e CDP restrito ao loopback. Compile e inicie o app com porta
   conhecida:
   ```sh
   scons frontend
   VECTORA_ELECTRON_CDP_PORT=9222 uv run vectora start
   ```
   O setup autentica pela ponte `vectora-app://` da própria janela, usando o
   backend conectado ao Electron. Os testes conectam ao Chromium incluído;
   Playwright é somente o cliente CDP e nenhum browser adicional é instalado.
   A suíte cria usuários e dados reais: inicie o Electron e o backend com um
   `VECTORA_HOME` isolado. Definir essa variável apenas no shell dos testes não
   isola um app/backend que já esteja rodando.
2. **Chave de provider** configurada (ex.: `GOOGLE_API_KEY`) — alguns testes
   exercitam o LLM real.
3. **Credenciais e2e** (opcional; há defaults). Numa instalação limpa (sem
   usuários), o `global-setup` cria o root automaticamente:
   ```
   E2E_EMAIL=e2e@vectora.local
   E2E_PASSWORD=Vectora-e2e-2026!
   ```
   Num backend que já tem usuários, defina-as batendo com um usuário válido.

## Rodar

```sh
VECTORA_ELECTRON_CDP_PORT=9222 pnpm --dir frontend test:e2e
VECTORA_ELECTRON_CDP_PORT=9222 pnpm --dir frontend test:e2e:ui
```

A porta CDP deve ser a mesma usada ao iniciar o Vectora. Os testes conectam à
janela Electron e navegam pela origem `vectora-app://app/`, exercitando o
Chromium e o renderer que o app realmente distribui.

> Estes testes **não** entram no `scons tests` (exigem Electron em execução,
> backend e, em alguns casos, LLM real). Rode-os sob demanda no fluxo de QA.

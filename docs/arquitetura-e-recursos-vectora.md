# Arquitetura e recursos do Vectora

## Visão do produto

O Vectora é um harness de engenharia de software orientado por agentes. O modelo de linguagem fornece raciocínio e geração de tokens; o Vectora fornece o sistema operacional do agente: contexto, memória, ferramentas, execução, permissões, persistência, observabilidade e verificação. Essa separação permite que modelos hospedados, BYOK, locais ou executados em serviços compatíveis usem o mesmo ciclo de trabalho.

Este documento consolida a arquitetura funcional do Vectora, as capacidades expostas pela implementação, o histórico registrado no changelog e a direção das issues e PRs abertas. A descrição de produto considera implementados os recursos da plataforma descritos no repositório. VEXT, Vectora 2.0 e a migração do backend para Rust permanecem como evolução planejada e não são apresentados como parte do runtime atual.

## Arquitetura em camadas

```text
Interface web ou desktop
        |
        v
React + Vite + TanStack Router + Zustand + i18n Paraglide
        |
        v
FastAPI, SSE, autenticação, RBAC e contratos Pydantic
        |
        v
Motor nativo de conversa
loop imperativo -> ChatClient -> tools -> resultados -> próximo turno
        |
        +--> providers de modelos, mídia e embeddings
        +--> SessionStore, memória, RAG, Context Graph e artifacts
        +--> workbenches e integrações externas
        +--> sandbox, HITL, budgets e telemetria
        |
        v
SQLite/LanceDB ou PostgreSQL/Qdrant/Redis/NATS
        |
        v
Electron, Chromium, sidecars e Docker quando necessários
```

O backend permanece ativo tanto no modo web quanto no desktop. No modo web, a SPA é servida por FastAPI e o Electron pode funcionar oculto como fornecedor de Chromium, Node embutido, IPC e CDP. No modo desktop, a mesma aplicação é apresentada em uma janela Electron e conversa com o backend por named pipe ou Unix socket, sem transformar o IPC em uma API TCP.

### Monorepo

- `vectora/`: produto principal, com backend Python, frontend React, Electron e testes.
- `services/`: workers e integrações de gateway e distribuição de atualizações.
- `company/`: superfícies comerciais e administrativas.
- `docs/`: documentação multilíngue.
- `SConstruct`: orquestração de instalação, lint, testes, frontend, sidecars e releases.

## Motor de agente

O ponto de entrada é `backend/engine/conversation_loop.py::run_conversation`. O motor usa um loop `while` explícito, relê o estado persistido, chama o cliente de chat em streaming, executa chamadas de ferramentas, publica eventos e repete até concluir, atingir um budget, ser interrompido ou falhar com diagnóstico observável.

O `agent_factory` monta o agente por usuário, modo e workspace. Ele resolve o `TOOL_REGISTRY`, aplica políticas de ferramentas, liga subagentes e HITL e injeta o contexto tipado. A arquitetura não depende de um grafo compilado: tools são a extensão principal e cada chamada é independentemente validável.

O ciclo suporta:

- streaming de texto, chamadas de tools, resultados, tarefas, perguntas, aprovações, métricas, arquivos e erros;
- execução assíncrona de lotes de tools com associação estável entre chamada e resultado;
- compactação de contexto preservando instruções, tool calls e resultados pareados;
- detecção de chamadas repetidas e loops presos;
- budgets de tokens, ferramentas, subagentes, rede, mídia e turnos;
- quality gates e juiz LLM para objetivos que precisam de verificação;
- retomada após aprovação humana, interrupção e falhas transitórias;
- cancelamento autenticado de tarefas e watchdog de liveness para subagentes.

### Subagentes e SOULs

O catálogo de agentes especializados inclui coder, search, reviewer, tester, devops, writer-docs, data-analyst, security-auditor, browser-qa e planner. Cada SOUL possui identidade, descrição, prompt, orçamento e conjunto de tools aplicado no binding de function-calling.

Subagentes recebem subthreads rastreáveis, correlation IDs, tokens de capacidade e o mesmo contexto de segurança do agente pai. Workspaces e operações Git que alteram arquivos podem usar worktrees isoladas. A política do agente principal é propagada e uma SOUL não pode usar tools fora do escopo RBAC.

## Tools e extensibilidade

O Vectora oferece mais de 160 tools nativas, registradas por schemas derivados de assinaturas e docstrings. A camada de tools é schema-first, assíncrona e defensiva: falhas viram resultados textuais para o agente, com logs estruturados, em vez de derrubar a conversa.

### Categorias principais

| Categoria     | Recursos                                                                                                     |
| ------------- | ------------------------------------------------------------------------------------------------------------ |
| Arquivos      | leitura, escrita, edição, busca, listagem, artifacts e hooks pós-escrita                                     |
| Terminal      | comandos locais ou remotos, sessões persistentes, streaming, timeout e redaction                             |
| Git           | status, log, diff, branches, checkout, commit, push, pull, stash, merge, worktrees e PRs                     |
| GitHub        | issues, PRs, comentários, diffs e merge via CLI/API                                                          |
| Web           | busca, fetch, crawl, map, extração, preview e YouTube                                                        |
| Browser       | navegação, DOM, screenshots, cliques, formulários, uploads, dialogs, tabs, console, rede, tracing e DevTools |
| RAG           | ingestão, embeddings, busca vetorial, busca textual, híbrida e retriever                                     |
| Context Graph | construção, consulta, explicação, caminhos, impacto e atualização                                            |
| Memória       | salvar, consultar, remover, consolidar e versionar fatos, preferências e skills                              |
| Workspace     | inventário, descrição, contexto, buckets e arquivos de configuração                                          |
| Planejamento  | planos persistentes, todos, perguntas estruturadas e pensamento sequencial                                   |
| Automação     | tarefas únicas, recorrentes, webhooks, boards, histórico e retomada                                          |
| Mídia         | geração/análise de imagem e vídeo, áudio, transcrição e texto para fala                                      |
| Integrações   | Gmail, Drive, Slack, Linear, Jira, Notion, Home Assistant e outros                                           |
| MCP           | consumo de servidores externos por stdio, HTTP e SSE                                                         |
| Utilitárias   | tempo, hashes, base64, regex, JSON, JWT e HTTP controlado                                                    |

O registry também agrupa tools por capacidade, resolve grupos compostos, impede ciclos e permite políticas por usuário, workspace, plugin e escopo global.

### MCP e Library

O Vectora atua como cliente MCP. Ele descobre, instala, atualiza e remove conectores de catálogos local, curado, enterprise e oficial. As entradas são validadas, versionadas por lockfile, verificadas por digest e classificadas por confiança. Credenciais não aparecem em previews, URLs ou auditoria.

Skills e plugins podem ser instalados em escopos de usuário, workspace, projeto ou runtime. A resolução usa frontmatter, SemVer, dependências, ciclos, política de sandbox e allowlists. O carregamento de schemas pode ser adiado para reduzir o custo de contexto.

## Workbenches

O workbench é o ambiente operacional compartilhado entre usuário e agente. Ele existe em modo Assistente, com chat como superfície principal, e modo IDE, com editor e painéis ancorados. A capacidade é a mesma nos dois layouts.

### Arquivos

Árvore do workspace, criação de arquivos e pastas, busca, refresh, renomeação, exclusão, lixeira, fixação, histórico Git, Monaco, janelas flutuantes e visualização de texto, imagem, áudio, vídeo e documentos.

### Git e Diff

Mudanças staged, unstaged e untracked, patches por arquivo, stage/unstage, histórico de commits, branches, sync, stash, worktrees, comparação e criação de PR. O estado é sempre derivado do backend e do repositório atual.

### Terminal

PTY real com xterm.js, múltiplas sessões, nomes, leitura e escrita assíncronas, buffers consultáveis, redaction e integração com as sessões persistentes de terminal do agente.

### Preview e Browser

Servidores de desenvolvimento são descobertos por configuração ou adicionados manualmente, iniciados, acompanhados e encerrados pelo workbench. O Browser usa WebContentsView e Chromium do Electron, com perfis persistentes e sessões isoladas por workspace. O backend controla páginas via Playwright conectado ao CDP já aberto pelo Electron.

As tools cobrem navegação, leitura de DOM e acessibilidade, formulários, upload, screenshots, console, rede, tracing, heap snapshots, Lighthouse e políticas para dialogs. A criação de abas é responsabilidade da UI/ponte do Electron; o CDP embutido não é tratado como se pudesse criar uma WebContentsView arbitrária.

### Memory e Context Graph

Memory exibe buscas RAG, resultados web, buckets, fatos, skills e consolidações. Context Graph constrói AST, relações semânticas, clusters e impacto; permite consultas, caminhos, explicações, exportação e perguntas sugeridas.

### Tasks, Search e Plan

Tasks mostra rotinas, webhooks, execuções, logs e threads de resultado. Search pesquisa o workspace com busca literal ou regex. Plan lista artifacts e arquivos tocados, mantendo o plano revisável e persistido.

## Providers e modelos

O roteamento unificado conecta clientes para OpenAI, Anthropic, Google, Cohere, Ollama, OpenRouter, 9Router, Voyage e llama.cpp. O catálogo de modelos registra provider, modelo, modalidade, origem, metadados e capacidades declaradas.

O sistema suporta:

- modelos remotos, BYOK e serviços OpenAI-compatible;
- Ollama local e Docker Model Runner;
- llama.cpp externo ou runtime gerenciado pelo Vectora;
- descoberta de GGUF no Hugging Face, seleção de variante, download, retomada, cancelamento, checksum e espaço em disco;
- instalação e ciclo de vida de sidecars locais;
- fallback de imagem quando o modelo ativo não aceita visão;
- seleção independente de chat, embeddings, reranking, transcrição e mídia;
- identificação de cobrança BYOK e separação de quota compartilhada;
- testes de conexão, readiness, `/v1/models` e diagnóstico de provider.

A configuração é centralizada em um registry declarativo. O wizard valida conexão antes de salvar credenciais, modelo e provider ativo. Chaves aparecem mascaradas e alterações atualizam ambiente, `.env`, settings e caches sem exigir reinício.

## Persistência, memória e RAG

O modo `lite` usa SQLite, LanceDB e NATS ou memória local. O modo `complete` adiciona PostgreSQL, Qdrant e Redis. Usuários, sessões, auditoria e settings continuam em SQLite para que a identidade local não dependa de serviços distribuídos.

O storage oferece:

- migrations idempotentes por checksum, histórico e detecção de drift;
- SessionStore nativo para mensagens, tool calls, resultados e retomadas;
- checkpoints, artifacts, eventos SSE e tombstones de threads;
- LanceDB local ou Qdrant remoto, busca densa, textual e híbrida;
- filas persistentes de embeddings, retries, dead-letter queue, circuit breaker e reconciliação;
- ingestão de diretórios com `.gitignore`, `.vectoraignore`, filtros, chunks, limites e progresso;
- buckets RAG ativáveis, reindexáveis, protegidos contra exclusão em uso e publicáveis;
- memória de longo prazo com fatos, decisões, armadilhas, preferências, versões e aprovação de consolidação;
- exportação, backup, restauração e migração entre backends.

## Segurança e governança

O Vectora combina autenticação, RBAC, filesystem policy, HITL, sandbox e telemetria.

- Auth suporta signup, signin, refresh, signout, recuperação de senha, cookies httpOnly, JWT, tokens de serviço e OIDC/OAuth com PKCE e JWKS.
- Roles root, admin, member e viewer controlam recursos, tools e administração.
- A política de filesystem avalia regras em ordem, nega por padrão e bloqueia `.env`, chaves privadas, `.ssh`, `.aws` e pseudo-filesystems.
- Aprovações persistem metadados seguros, allowlists por workspace, expiração, idempotência e revogação por identificador opaco.
- O modo de permissão pode perguntar, aceitar edições, planejar, executar automaticamente ou exigir revisão.
- Prompt injection é tratado como conteúdo não confiável quando chega de arquivos, páginas ou resultados de tools.
- Sandbox pode usar local, Docker, SSH, Modal, macOS Seatbelt, Singularity ou Linux nativo, com caminhos read-only/read-write, máscaras, quotas, timeout, rlimits, namespaces, seccomp e rede controlada.
- O modo de desktop usa IPC e não abre TCP para o renderer; o modo web expõe a API autenticada.
- Telemetria redige tokens, cookies, senhas, headers e argumentos sensíveis, mantendo métricas agregadas e auditáveis.

## Automação, integrações e operação

O scheduler cria tarefas únicas, recorrentes, manuais ou acionadas por webhook. Cron e linguagem natural respeitam o fuso do usuário; runs têm orçamento, prioridade, retries, watchdog, cancelamento, retomada e quality gates. GitHub, GitLab, Linear, Slack e e-mail podem alimentar tarefas e Kanban por eventos idempotentes.

O lifecycle do servidor aplica migrations, inicia cache/KV, NATS, workers de embedding, scheduler, consolidação de memória, gateway e Electron, e encerra sidecars com timeout e cleanup. Docker Compose fornece PostgreSQL, Redis e Qdrant locais para o modo complete. Atualizações desktop usam manifests, hashes, canais por plataforma, rollout gradual, backups rotativos e rollback.

## Frontend e experiência

O frontend é uma SPA React com Vite, TanStack Router, Zustand, Paraglide i18n e componentes de workbench. O backend serve os bundles estáticos em produção. O estado crítico vem do backend; caches usam stale-while-revalidate.

A interface inclui onboarding, autenticação, sessões Chat e Dev, seleção de workspace, modelos e providers, settings, Library, ferramentas, plugins, Skills, memória, Context Graph, Kanban, feedback, administração, billing, atualizações e compartilhamento de threads.

Mensagens multimodais, attachments, tool calls, perguntas, aprovações, arquivos alterados, citations, planos, subagentes e métricas são renderizados a partir de eventos tipados. A aplicação mantém histórico de threads e indicadores de atividade baseados em respostas persistidas do agente.

## Testes e verificabilidade

A estratégia combina TDD, testes de erro e validação E2E. O monorepo usa SCons para executar backend, frontend, Electron, services e company com dependências lockadas.

- Backend: pytest unitário, integração, lifecycle, storage, browser, sandbox, local LLM, stress e E2E.
- Frontend: Vitest, Testing Library, typecheck, oxlint, testes de stores, hooks, rotas, streaming e workbenches.
- Electron: testes de lifecycle, IPC, CDP, browser views, Docker CLI, runtime profile, cookies e updates.
- Infra: Docker Compose, NATS, Redis, PostgreSQL, Qdrant, sidecars, health checks, crash recovery e shutdown.
- Providers: descoberta, readiness, inferência, streaming, multimodalidade, tool calling, fallback e diagnóstico.
- Contratos: schemas Pydantic, eventos SSE, registry de tools, storage e adapters vetoriais.

A suíte deve cobrir caminho feliz e erro para vazio, nulo, duplicado, ordem trocada, payload inválido, timeout, cancelamento, concorrência, restart e perda de dependência. Os testes são a reconstrução executável dos contratos do produto.

## Estado implementado e evolução planejada

### Implementado na visão de produto

O runtime atual reúne agente nativo, tools, subagentes, workbenches, providers, storage lite/complete, RAG, memória, Context Graph, sandbox, HITL, autenticação, integrações, automação, desktop/web, Docker e pipeline de testes. A linha 0.3.0 do changelog registra essa superfície e as correções de lifecycle, providers locais, modelos, browser, storage e release.

### Issues abertas de evolução

| Issue | Tema                      | Direção                                                                              |
| ----- | ------------------------- | ------------------------------------------------------------------------------------ |
| #72   | Git Workbench             | aproximar fluxo do GitHub Desktop, com operações mais previsíveis e observáveis      |
| #79   | GH Bot                    | integrar review bot ao ecossistema Vectora e à API privada                           |
| #271  | Monaco e Godot            | ampliar linguagens, temas e experiência de edição                                    |
| #301  | Godot tooling             | adicionar análise semântica e automação específica de projetos Godot                 |
| #304  | Contratos backend         | consolidar schemas, eventos, storage e registry                                      |
| #309  | Perfis e settings         | isolar dados por usuário e adotar `settings.json` editável                           |
| #310  | Context Graph             | evoluir visualização, comunidades e paridade com Graphify                            |
| #317  | llama.cpp e Hugging Face  | ampliar paridade de instalação, catálogo, runtime e providers locais                 |
| #318  | Decision Models           | integrar JEV e Clef como categoria independente de modelos                           |
| #330  | Pensamento no streaming   | tornar o pensamento do agente visível durante execuções longas                       |
| #350  | Vectora Pro e open source | completar entitlements e separar os limites de publicação                            |
| #351  | NATS e Redis              | elevar paridade sem perder a diferença entre lite e complete                         |
| #352  | SQL e vetores             | definir contratos canônicos e paridade SQLite/PostgreSQL/LanceDB/Qdrant              |
| #353  | Release e CI              | tornar branches, milestones, reruns e artefatos determinísticos                      |
| #354  | Bootstrap                 | unificar instalação, doctor, sidecars e lifecycle do dev stack                       |
| #366  | Search e Browser          | migrar pesquisa para Chromium/Electron e retirar a dependência operacional de Tavily |

### PRs abertas relevantes

| PR   | Conteúdo                                              |
| ---- | ----------------------------------------------------- |
| #329 | release 0.3.0                                         |
| #340 | release 0.2.1                                         |
| #348 | fundação do ecossistema VEXT e host isolado           |
| #355 | providers locais, modelos e integração de workbenches |

## Roadmap explicitamente planejado

### VEXT

VEXT é o formato de extensão do Vectora. O plano inclui artefato versionado e assinado, SDKs, builder CLI, manifesto de capacidades, compatibilidade, catálogo, scopes de instalação e host isolado. A extensão deve executar com permissões declaradas, dependências verificadas, frontend e backend separados e políticas de confiança. Esse trabalho é roadmap e não parte do runtime atual descrito acima.

### Vectora 2.0 e backend Rust

Vectora 2.0 é a migração arquitetural de longo prazo para Rust. O objetivo é preservar contratos de API, eventos, tools, persistência, segurança e compatibilidade enquanto se substituem componentes Python de maior custo operacional. A migração deve ocorrer por fronteiras estáveis, com adapters, testes de contrato, execução paralela e rollback; não é uma reescrita implícita do produto 0.3.

### Pesquisa e navegação nativas

A direção de #366 é fazer o Electron Chromium ser o runtime único de navegação: Playwright controla o CDP existente, o Browser Workbench e o agente compartilham ownership de tabs e perfis, e search/fetch/crawl/map usam contratos tipados, limites, cancelamento, SSRF guard e diagnóstico uniforme. Tavily pode ser mantido apenas durante a migração de paridade, depois removido da superfície operacional.

## Referências de engenharia

- `vectora/CHANGELOG.md` e `docs/content/` — capacidades, guias e contratos publicados.
- `backend/engine/`, `backend/tools/`, `backend/services/`, `backend/storage/` — runtime do agente, tools, lifecycle e persistência.
- `frontend/components/`, `frontend/src/routes/`, `frontend/electron/` — UI, workbenches e integração desktop.
- Issues abertas `#72`, `#79`, `#233`, `#266`, `#271`, `#301`, `#304`, `#309`, `#310`, `#317`, `#318`, `#330`, `#350`, `#351`, `#352`, `#353`, `#354` e `#366`.
- PRs abertas `#329`, `#340`, `#348` e `#355`.
- Documentos de referência sobre AI Coding Harness fornecidos para esta síntese.

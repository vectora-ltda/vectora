---
title: Novidades da linha 0.3
weight: 1
---

A linha `release/0.3` amplia o Vectora de um workspace com ferramentas para um runtime de agente com execuções identificáveis, evidências, planejamento, políticas e avaliação. Esta página descreve a composição da linha, incluindo o PR #404 empilhado sobre ela. A tag `0.3.0` ainda precisa ser publicada para que o conteúdo seja considerado uma release distribuída.

## O que há de novo

### Execuções persistentes e histórico

O AgentRun associa estado, identificador `run_id`, evidências e relações entre agente principal e subagentes. O ledger de eventos sustenta o histórico da thread e sua reconstrução. O armazenamento local usa SQLite; instalações distribuídas podem configurar PostgreSQL. Checkpoints e recursos de retomada dependem do caminho de execução e do storage configurado.

Consulte [sessões e workspaces](../../concepts/sessions-and-workspaces) e a [referência de agentes](../../reference/agents).

### Planos, subagentes e Kanban

O Plan Mode pode decompor uma solicitação em etapas executáveis e dependências. O Kanban organiza tarefas em background e acompanha suas execuções; políticas e gates continuam determinando o que exige aprovação. Delegação paralela é adequada quando as tarefas são independentes e cada perfil tem somente as ferramentas permitidas.

Consulte [subagentes](../../concepts/sub-agents), [automação de agentes](../agent-automation) e [uso do chat](../using-the-chat). O guia [Plan Mode, AgentRun e decisões](../plan-mode-and-agentrun) detalha o Plan Mode, os estados do AgentRun e as decisões registradas.

### HITL e Decision Models configuráveis

No PR #404, a aba **Configurações → Modelos de IA** reúne providers de IA e as superfícies que podem consultar Decision Models. O menu de Decision Model e o painel de HITL ficam acessíveis no chat. Clef, Laya, JEV ou outro provider compatível pode produzir uma decisão estruturada `allow`, `deny` ou `requires_human`.

Para configurar:

1. Abra **Configurações → Modelos de IA** e selecione um provider de decisão.
2. Configure credenciais e confira a prontidão reportada pelo runtime.
3. Habilite apenas as superfícies em que deseja consultar o modelo.
4. No chat, escolha se o modelo configurado será usado naquela execução.
5. Confirme que uma decisão de modelo não substitui policy, permissões nem gates de aprovação.

Uma avaliação iniciada que falhe não deve liberar a ação. Modelo ausente ou indisponível não conta como decisão aprovada; os controles estáticos continuam ativos. Veja o guia de [Decision Models](../decision-models) e [configurações](../using-settings).

### Verificação e Evaluation Lab

Quality gates avaliam resultados após a execução e distinguem aprovado, falho, bloqueado, degradado e não verificado. Cassetes registram interações com redação de dados sensíveis; replay permite comparar uma execução gravada sem chamar novamente o provider externo.

Use `vectora eval run` para executar um cassette e `vectora eval compare` para comparar resultados. O replay valida o comportamento reproduzível do cassette, não a disponibilidade live do modelo, Browser Workbench ou serviço externo. Veja [Verificação e Evaluation Lab](../verification-lab) para configurar os perfis de quality gates.

### Memória, contexto e RAG

Context Planner e Context Graph organizam contexto do projeto; memória persistente e buckets permitem separar conjuntos de conhecimento. A ingestão indexa documentos para recuperação vetorial e, conforme o modo de storage e os providers configurados, recuperação híbrida e reranking.

Adicione documentos ao workspace, aguarde o processamento da fila de embeddings e confira o resultado de busca antes de depender da base. Consulte [RAG](../../concepts/rag), [Context Graph](../../concepts/context-graph) e [storage](../../concepts/storage).

### Browser Workbench e policy/egress

Quando o sidecar Electron e a sessão preparada pela UI estão ativos, o runtime usa o Chromium integrado para pesquisa e navegação. Search, fetch, crawl e map compartilham os controles aplicáveis de URL e destino; limites de páginas, profundidade e origem restringem a coleta. O fallback HTTP não é a mesma sessão do navegador e não herda cookies.

Policy snapshots e verificações de egress aplicam regras aos caminhos de ferramentas conectados. A cobertura é específica de cada adapter; uma integração não conectada não herda controles por estar descrita nesta página. Consulte [Políticas de egress](../egress-policies), [workbench](../using-the-workbench) e [segurança](../../security).

## Como validar no seu ambiente

- Confira a versão instalada e a prontidão de cada provider antes de iniciar uma execução.
- Faça uma execução simples no chat; depois teste aprovação, bloqueio e erro com uma ação controlada.
- Para providers locais, confirme que o serviço e os pesos/modelos necessários estão disponíveis.
- Identifique resultados `passed`, `failed` e `skipped` separadamente. Um teste `skipped` não valida o caminho live.
- Não use replay como substituto de uma chamada real quando a integração live for o objetivo do teste.

Os PRs [#382](https://github.com/vectora-ltda/vectora/pull/382), [#400](https://github.com/vectora-ltda/vectora/pull/400), [#404](https://github.com/vectora-ltda/vectora/pull/404), [#408](https://github.com/vectora-ltda/vectora/pull/408) e [#414](https://github.com/vectora-ltda/vectora/pull/414) documentam a composição e os complementos da linha. A tag e os artefatos publicados são a referência final para disponibilidade.

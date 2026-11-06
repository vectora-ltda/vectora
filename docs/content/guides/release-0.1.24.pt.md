---
title: Novidades da versão 0.1.24
weight: 1
---

A versão 0.1.24 reúne a evolução do Vectora de um workspace com ferramentas para um runtime de agente com execuções identificáveis, evidências, planejamento, políticas e avaliação. Esta página descreve as capacidades que compõem essa versão.

## O que há de novo

As seções abaixo agrupam as capacidades da linha por área, com os limites que valem para cada uma.

### Execuções persistentes e histórico

O AgentRun associa estado, identificador `run_id`, evidências e relações entre agente principal e subagentes. O ledger de eventos sustenta o histórico da thread e sua reconstrução. O armazenamento local usa SQLite; instalações distribuídas podem configurar PostgreSQL. Checkpoints e recursos de retomada dependem do caminho de execução e do storage configurado.

Consulte [sessões e workspaces](../../concepts/sessions-and-workspaces) e a [referência de agentes](../../reference/agents).

### Planos, subagentes e Kanban

O Plan Mode pode decompor uma solicitação em etapas executáveis e dependências. O Kanban organiza tarefas em background e acompanha suas execuções; políticas e gates continuam determinando o que exige aprovação. Delegação paralela é adequada quando as tarefas são independentes e cada perfil tem somente as ferramentas permitidas.

Consulte [subagentes](../../concepts/sub-agents), [automação de agentes](../agent-automation) e [uso do chat](../using-the-chat). O guia [Plan Mode, AgentRun e decisões](../plan-mode-and-agentrun) detalha o Plan Mode, os estados do AgentRun e as decisões registradas.

### HITL e Decision Models configuráveis

Para configurar:

1. Abra **Configurações → Modelos de IA** e selecione um provider de decisão.
2. Configure credenciais e confira a prontidão reportada pelo runtime.
3. Habilite apenas as superfícies em que deseja consultar o modelo.
4. No chat, escolha se o modelo configurado será usado naquela execução.
5. Confirme que uma decisão de modelo não substitui policy, permissões nem gates de aprovação.

Uma avaliação iniciada que falhe não deve liberar a ação. Modelo ausente ou indisponível não conta como decisão aprovada; os controles estáticos continuam ativos. Veja o guia de [Decision Models](../decision-models) e [configurações](../using-settings).

### Verificação e Evaluation Lab

Quality gates avaliam resultados após a execução e distinguem aprovado, falho, bloqueado, degradado e não verificado. A CLI não captura interações live diretamente. Habilite `EVALUATION_RECORDING_ENABLED=true` antes de executar para registrar as interações do modelo no AgentRun. Salve o JSON da resposta autenticada de `GET /runs/{run_id}` e use `vectora eval export-run --run-file run.json --target cassette.json`; o comando usa `cassette_from_run` e recusa execuções sem interações gravadas. Excluir a sessão remove o trace armazenado, mas os arquivos exportados exigem retenção e exclusão separadas. Apenas campos e padrões conhecidos de credenciais são ocultados; mensagens e respostas podem manter código privado ou dados pessoais. Restrinja o acesso e revise as cassetes antes de compartilhar.

Use `vectora eval run` para reproduzir uma única cassete e `vectora eval compare` para comparar resultados. O replay valida o comportamento reproduzível do cassette, não a disponibilidade live do modelo, Browser Workbench ou serviço externo. Veja [Verificação e Evaluation Lab](../verification-lab) para configurar os perfis de quality gates.

### Memória, contexto e RAG

Context Planner e Context Graph organizam contexto do projeto; memória persistente e buckets permitem separar conjuntos de conhecimento. A ingestão indexa documentos para recuperação vetorial e, conforme o modo de storage e os providers configurados, recuperação híbrida e reranking.

Adicione documentos ao workspace, aguarde o processamento da fila de embeddings e confira o resultado de busca antes de depender da base. Consulte [RAG](../../concepts/rag), [Context Graph](../../concepts/context-graph) e [storage](../../concepts/storage).

### Browser Workbench e policy/egress

A navegação do Browser Workbench usa Chromium do Electron quando o sidecar e a sessão do workspace preparada pela UI estão disponíveis. No caminho padrão Tavily, `fetch_url` usa Chromium como fallback sem chave de API ou após erros de quota/rate limit. `web_search` envia consultas ao provider configurado (por exemplo Tavily) e pode recorrer à API do DuckDuckGo. `web_crawl` e `web_map` exigem `TAVILY_API_KEY` e não têm fallback Chromium. Fetch, crawl e map verificam a URL inicial na política de egress; Search não aplica essa política de destino. O fallback Chromium não revalida redirects nem fixa o IP de destino.

Policy snapshots e verificações de egress aplicam regras aos caminhos de ferramentas conectados. A cobertura é específica de cada adapter; uma integração não conectada não herda controles por estar descrita nesta página. Consulte [Políticas de egress](../egress-policies), [workbench](../using-the-workbench) e [segurança](../../security).

## Como validar no seu ambiente

- Confira a versão instalada e a prontidão de cada provider antes de iniciar uma execução.
- Faça uma execução simples no chat; depois teste aprovação, bloqueio e erro com uma ação controlada.
- Para providers locais, confirme que o serviço e os pesos/modelos necessários estão disponíveis.
- Identifique resultados `passed`, `failed` e `skipped` separadamente. Um teste `skipped` não valida o caminho live.
- Não use replay como substituto de uma chamada real quando a integração live for o objetivo do teste.

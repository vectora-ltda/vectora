---
title: Modelos de Webhook
weight: 8
---

Uma tarefa em segundo plano com gatilho `webhook` transforma um evento externo numa execução do agente: um provider (GitHub, GitLab, Slack, Linear, email, ou sua própria ferramenta de alerta via o [endpoint de observabilidade](../observability-webhooks)) posta pra Vectora e, se existir uma tarefa casando com o evento, o agente acorda com o payload desse evento embutido na instrução. Esta página é a referência dos três modelos concretos que o Vectora já entrega hoje, em ordem crescente do que o agente de fato faz com o evento.

Essa capacidade exige uma assinatura **Pro** (automação de chat disparada de fora do produto) — veja as [assinaturas e os preços](https://vectora.company/#pricing).

## Como a ponte funciona

1. O provider posta em `POST /webhook/{provider}` (ou `POST /webhook/observability` para o contrato genérico de alerta). A assinatura é verificada contra uma env var secreta (`GITHUB_WEBHOOK_SECRET`, `GITLAB_WEBHOOK_TOKEN`, etc.) antes de qualquer outra coisa acontecer.
2. O Vectora procura tarefas em segundo plano **habilitadas** com `trigger_type: "webhook"` cujo `trigger_config` case com o evento — por exemplo `{"provider": "github", "events": ["pull_request"]}`. Várias tarefas casando disparam todas; nenhuma casando significa que o evento é persistido (visível no stream de eventos do workbench) mas nada roda.
3. A sessão e o workspace de cada tarefa que casou viram a casa dessa execução — o agente tem o histórico completo daquela sessão e o filesystem/git/tools daquele workspace disponíveis, igual a qualquer outra execução.
4. O payload do evento (truncado a 4000 caracteres) é anexado à instrução da tarefa como um bloco JSON, então o agente lê direto em vez de por um campo estruturado separado.

Você cria a tarefa do mesmo jeito que cria qualquer outra tarefa em segundo plano: pede ao agente no chat. Não existe um formulário separado de configuração de webhook — `trigger_config` é só JSON que o agente preenche a partir do que você conta pra ele.

## Modelo 1 — Revisão de PR do GitHub (tools determinísticas, julgamento do LLM)

O modelo bandeira: o agente lê o diff real de um PR e posta um comentário de revisão, inteiramente por conta própria.

**Configuração**: no chat, peça algo como:

> "Crie uma tarefa em segundo plano, disparada por webhooks `pull_request` do GitHub, que busca o diff do PR e posta um comentário curto de revisão — sinalize qualquer coisa que pareça insegura ou sem teste, senão diga que está tudo bem."

Isso cria uma tarefa com `trigger_type: "webhook"`, `trigger_config: {"provider": "github", "events": ["pull_request"]}`. Configure o webhook do seu repositório GitHub (**Settings → Webhooks → Add webhook**) apontando para `https://<seu-backend>/webhook/github` com content type `application/json`, e ajuste `GITHUB_WEBHOOK_SECRET` no backend pra combinar com o que você digitar como secret do webhook.

**O que roda**: o agente tem duas tools disponíveis para isso — `github_fetch_pr_diff(owner, repo, pr_number)`, que busca o diff unificado (o payload do webhook só traz metadados e URLs, não o diff em si), e `github_post_pr_comment(owner, repo, pr_number, body)`, que posta a revisão como um comentário de issue (PRs são issues por baixo dos panos no GitHub, então é o mesmo endpoint). As duas usam o `GITHUB_TOKEN` já configurado pela integração OAuth/PAT do GitHub — nenhuma credencial separada. Qualquer uma das tools falhando (repo renomeado, token sem o escopo `repo`, PR fechado à força no meio da execução) devolve um erro tipado que o agente vê e pode reagir, em vez de derrubar a execução; uma falha ao buscar o diff significa que o agente não tenta o comentário.

## Modelo 2 — Issues do GitHub → Kanban (determinístico, sem LLM)

Todo evento `opened`/`closed`/`reopened`/`edited`/`assigned` numa issue do GitHub espelha num card do Kanban — título, estado e um link de volta pra issue — sem invocar o LLM em momento algum. É um insert-or-update fixo contra a mesma tabela de tarefas que o resto do modelo do Kanban usa, indexado por `repo` + `issue_number`, então reentrega do mesmo evento atualiza o card existente em vez de duplicar.

**Configuração**: crie uma tarefa com `trigger_config: {"provider": "github", "events": ["issues"]}`. A sessão e o workspace dessa tarefa viram a casa de todo card derivado de issue. Sem essa tarefa habilitada, eventos de issue continuam sendo recebidos e persistidos, mas nenhum card é criado.

## Modelo 3 — Alertas de observabilidade → Kanban (determinístico, sem LLM)

Mesmo mecanismo do Modelo 2, mas para ferramentas de alerta (Sentry, Grafana, PagerDuty, ou qualquer coisa que consiga mandar um webhook) via o endpoint dedicado `/webhook/observability` e seu contrato de payload fixo. A severidade mapeia pra coluna inicial do card — `critical`/`high` cai em triage, o resto em todo. Referência completa de campos, detalhes de autenticação e configuração por provider (Sentry, Grafana, PagerDuty) estão em [Webhooks de Observabilidade](../observability-webhooks).

## Veja também

- [Webhooks de Observabilidade](../observability-webhooks) — contrato completo de payload e configuração de providers pro Modelo 3
- [Automação do Agente](../agent-automation) — agendamento, delegação e o Vectora Connect
- [Usando o Workbench](../using-the-workbench) — a aba Tasks onde essas execuções aparecem

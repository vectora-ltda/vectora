---
title: Automação do Agente
weight: 6
---

Além de responder no turno atual, o agente do Vectora consegue delegar trabalho a subagentes isolados, agendar trabalho pra depois, aprender padrões reutilizáveis de uma sessão, reagir a webhooks externos e receber mensagens de fora da UI do chat.

## Delegar — execução isolada em subagente

Quando o orquestrador delega uma tarefa de código pro subagente `coder`, ele pode rodar esse trabalho numa **git worktree** isolada em vez de direto no checkout do seu workspace principal. Isso significa que uma mudança longa ou exploratória não colide com arquivos que você está editando ativamente, e pode ser revisada ou descartada como branch própria antes do merge. A criação da worktree reusa a mesma lógica git que a tool `git_worktree` já expõe, então falha do mesmo jeito honesto (branch inválida, workspace sem git, disco cheio) em vez de deixar uma tarefa travada.

## Agendar — tarefas recorrentes e execuções avulsas de subagente

O agente pode agendar trabalho de duas formas:

- **Tarefas recorrentes** (`schedule_task`) — descritas em expressões de tempo em linguagem natural ("todo dia às 9h", "toda sexta às 18h", "a cada 2 horas"), parseadas deterministicamente pra um cron schedule. Uma expressão que não bate com um padrão conhecido nunca é chutada — volta como erro pedindo pra você reformular.
- **Execuções avulsas de subagente** (`schedule_subagent_task`) — agenda um subagente *específico* (`coder` ou `search`), não o orquestrador inteiro, pra rodar uma vez num momento futuro ("em 30 minutos", "em 2 horas"). Uma execução agendada de `coder` usa o mesmo isolamento de worktree do Delegar acima quando há um workspace ativo.

Ambas aparecem na aba Tasks do workbench, distinguindo execuções agendadas de trabalho acontecendo no turno atual.

## Remember — aprendizado automático, sempre com aprovação

A cada 5 turnos de uma conversa, o Vectora automaticamente revisa a transcrição em busca de padrões reutilizáveis — skills que valem a pena salvar, fatos que valem a pena lembrar — e, se encontrar algo, propõe na próxima vez que você interagir com aquela thread. Nada é escrito automaticamente: a proposta fica pendente até você aprovar ou rejeitar, e uma proposta pendente bloqueia um novo disparo automático até ser resolvida (então não empilha propostas repetidas).

Você também pode disparar isso manualmente, ou fazer o agente salvar um fato específico ou instalar uma skill específica diretamente — as duas ações exigem sua aprovação do mesmo jeito, e as duas deixam um artefato visível na **aba Plan** depois de aprovadas, então o que o Vectora aprendeu sobre seu projeto continua visível e consultável, não só um diff que rola pra fora da tela.

## Automação disparada por webhook

Além de agendamentos, uma tarefa em segundo plano também pode ser disparada por um webhook de entrada — um PR do GitHub abrindo, uma issue do GitHub mudando de estado, ou um alerta da sua stack de observabilidade. O payload do evento é embutido na instrução do agente, então ele lê o mesmo contexto que um humano colaria. Veja [Modelos de Webhook](../webhook-templates) pros modelos concretos que o Vectora entrega (revisão de PR, sync de issue, alertas de observabilidade) e [Webhooks de Observabilidade](../observability-webhooks) pro contrato genérico de alerta.

## Vectora Connect — recebendo mensagens de fora da UI do chat

O Vectora Connect entrega chat através de plataformas além da UI embutida — Telegram (long polling), Discord (WebSocket Gateway), Slack (Socket Mode) e email (IMAP/SMTP) estão implementados e rodando hoje, cada um traduzindo o formato nativo de mensagem daquela plataforma pro mesmo turno que a UI de chat embutida produz, depois respondendo de volta por aquela plataforma. Connect é uma feature **Pro** — veja os [preços](https://vectora.company/#pricing).

## Veja também

- [Modelos de Webhook](../webhook-templates) — os três modelos de automação disparados por webhook
- [Sessões e Workspaces](../../concepts/sessions-and-workspaces) — o que é um workspace e como a confiança funciona
- [Sandbox](../../concepts/sandbox) — sandboxing de acesso a terminal/arquivo por workspace
- [Usando o Workbench](../using-the-workbench) — as abas Tasks e Plan na prática

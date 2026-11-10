---
title: Automatización del Agente
weight: 6
---

Más allá de responder en el turno actual, el agente de Vectora puede delegar trabajo a subagentes aislados, programar trabajo para más tarde, aprender patrones reutilizables de una sesión, reaccionar a webhooks externos y recibir mensajes desde fuera de la UI de chat.

## Delegar — ejecución aislada en subagente

Cuando el orquestador delega una tarea de código al subagente `coder`, puede ejecutar ese trabajo en un **git worktree** aislado en lugar de directamente en el checkout de tu workspace principal. Esto significa que un cambio largo o exploratorio no choca con archivos que estás editando activamente, y puede revisarse o descartarse como su propia rama antes de fusionarse. La creación del worktree reutiliza la misma lógica git que ya expone la tool `git_worktree`, así que falla de la misma forma honesta (rama inválida, workspace sin git, disco lleno) en lugar de dejar una tarea atascada.

## Programar — tareas recurrentes y ejecuciones puntuales de subagente

El agente puede programar trabajo de dos formas:

- **Tareas recurrentes** (`schedule_task`) — descritas en expresiones de tiempo en lenguaje natural ("todos los días a las 9am", "todos los viernes a las 6pm", "cada 2 horas"), parseadas de forma determinista a un cron schedule. Una expresión que no coincide con un patrón conocido nunca se adivina — vuelve como error pidiendo que la reformules.
- **Ejecuciones puntuales de subagente** (`schedule_subagent_task`) — programa un subagente *específico* (`coder` o `search`), no el orquestador completo, para ejecutarse una vez en un momento futuro ("en 30 minutos", "en 2 horas"). Una ejecución programada de `coder` usa el mismo aislamiento de worktree que Delegar arriba cuando hay un workspace activo.

Ambas aparecen en la pestaña Tasks del workbench, distinguiendo ejecuciones programadas del trabajo que ocurre en el turno actual.

## Remember — aprendizaje automático, siempre con aprobación

Cada 5 turnos de una conversación, Vectora revisa automáticamente la transcripción en busca de patrones reutilizables — skills que vale la pena guardar, hechos que vale la pena recordar — y, si encuentra algo, lo propone la próxima vez que interactúes con ese hilo. Nada se escribe automáticamente: la propuesta queda pendiente hasta que la apruebes o rechaces, y una propuesta pendiente bloquea un nuevo disparo automático hasta que se resuelva (así no se acumulan propuestas repetidas).

También puedes disparar esto manualmente, o hacer que el agente guarde un hecho específico o instale una skill específica directamente — ambas acciones requieren tu aprobación de la misma forma, y ambas dejan un artefacto visible en la **pestaña Plan** una vez aprobadas, así que lo que Vectora aprendió sobre tu proyecto sigue siendo visible y consultable, no solo un diff que se pierde en el scroll.

## Automatización disparada por webhook

Más allá de los horarios, una tarea en segundo plano también puede dispararse por un webhook entrante — un PR de GitHub abriéndose, un issue de GitHub cambiando de estado, o una alerta de tu stack de observabilidad. El payload del evento se incrusta en la instrucción del agente, así que este lee el mismo contexto que pegaría un humano. Consulta [Plantillas de Webhook](../webhook-templates) para los modelos concretos que ofrece Vectora (revisión de PR, sincronización de issues, alertas de observabilidad) y [Webhooks de Observabilidad](../observability-webhooks) para el contrato genérico de alertas.

## Vectora Connect — recibiendo mensajes desde fuera de la UI de chat

Vectora Connect entrega chat a través de plataformas además de la UI integrada — Telegram (long polling), Discord (WebSocket Gateway), Slack (Socket Mode) y email (IMAP/SMTP) están implementados y funcionando hoy, cada uno traduciendo el formato nativo de mensajes de esa plataforma al mismo turno que produce la UI de chat integrada, y respondiendo de vuelta por esa plataforma. Connect es una función **Pro** — consulta los [precios](https://vectora.company/#pricing).

## Ver también

- [Plantillas de Webhook](../webhook-templates) — los tres modelos de automatización disparados por webhook
- [Sesiones y Workspaces](../../concepts/sessions-and-workspaces) — qué es un workspace y cómo funciona la confianza
- [Sandbox](../../concepts/sandbox) — sandboxing de acceso a terminal/archivos por workspace
- [Usando el Workbench](../using-the-workbench) — las pestañas Tasks y Plan en la práctica

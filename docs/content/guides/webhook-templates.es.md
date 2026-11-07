---
title: Plantillas de Webhook
weight: 8
---

Una tarea en segundo plano con disparador `webhook` convierte un evento externo en una ejecución del agente: un proveedor (GitHub, GitLab, Slack, Linear, email, o tu propia herramienta de alertas vía el [endpoint de observabilidad](../observability-webhooks)) envía una petición a Vectora y, si existe una tarea que coincide con el evento, el agente despierta con el payload de ese evento incrustado en la instrucción. Esta página es la referencia de los tres modelos concretos que Vectora ofrece hoy, en orden creciente de lo que el agente realmente hace con el evento.

Esta capacidad requiere el plan **Pro** (automatización de chat disparada desde fuera del producto) — consulta los [precios](https://vectora.company/#pricing).

## Cómo funciona el puente

1. El proveedor envía la petición a `POST /webhook/{provider}` (o `POST /webhook/observability` para el contrato genérico de alertas). La firma se verifica contra una variable de entorno secreta (`GITHUB_WEBHOOK_SECRET`, `GITLAB_WEBHOOK_TOKEN`, etc.) antes de que ocurra cualquier otra cosa.
2. Vectora busca tareas en segundo plano **habilitadas** con `trigger_type: "webhook"` cuyo `trigger_config` coincida con el evento — por ejemplo `{"provider": "github", "events": ["pull_request"]}`. Si varias tareas coinciden, todas se disparan; si ninguna coincide, el evento se persiste (visible en el stream de eventos del workbench) pero no se ejecuta nada.
3. La sesión y el workspace de cada tarea que coincidió se convierten en la base de esa ejecución — el agente tiene el historial completo de esa sesión y el filesystem/git/tools de ese workspace disponibles, igual que en cualquier otra ejecución.
4. El payload del evento (truncado a 4000 caracteres) se añade a la instrucción de la tarea como un bloque JSON, así que el agente lo lee directamente en lugar de a través de un campo estructurado separado.

Creas la tarea de la misma forma que cualquier otra tarea en segundo plano: se lo pides al agente en el chat. No existe un formulario separado de configuración de webhook — `trigger_config` es solo JSON que el agente completa a partir de lo que le cuentes.

## Modelo 1 — Revisión de PR de GitHub (tools determinísticas, criterio del LLM)

El modelo insignia: el agente lee el diff real de un PR y publica un comentario de revisión, completamente por su cuenta.

**Configuración**: en el chat, pide algo como:

> "Crea una tarea en segundo plano, disparada por webhooks `pull_request` de GitHub, que obtenga el diff del PR y publique un comentario breve de revisión — señala cualquier cosa que parezca insegura o sin pruebas, si no, di que está todo bien."

Esto crea una tarea con `trigger_type: "webhook"`, `trigger_config: {"provider": "github", "events": ["pull_request"]}`. Configura el webhook de tu repositorio de GitHub (**Settings → Webhooks → Add webhook**) apuntando a `https://<tu-backend>/webhook/github` con content type `application/json`, y ajusta `GITHUB_WEBHOOK_SECRET` en el backend para que coincida con lo que escribas como secreto del webhook.

**Qué se ejecuta**: el agente tiene dos tools disponibles para esto — `github_fetch_pr_diff(owner, repo, pr_number)`, que obtiene el diff unificado (el payload del webhook solo trae metadatos y URLs, no el diff en sí), y `github_post_pr_comment(owner, repo, pr_number, body)`, que publica la revisión como un comentario de issue (los PRs son issues por debajo en GitHub, así que es el mismo endpoint). Ambas usan el `GITHUB_TOKEN` ya configurado mediante la integración OAuth/PAT de GitHub — sin credencial separada. Si cualquiera de las tools falla (repo renombrado, token sin el scope `repo`, PR cerrado a la fuerza a mitad de la ejecución) devuelve un error tipado que el agente ve y puede manejar, en lugar de romper la ejecución; un fallo al obtener el diff significa que el agente no intenta el comentario.

## Modelo 2 — Issues de GitHub → Kanban (determinístico, sin LLM)

Cada evento `opened`/`closed`/`reopened`/`edited`/`assigned` en un issue de GitHub se refleja en una tarjeta del Kanban — título, estado y un enlace de vuelta al issue — sin invocar el LLM en ningún momento. Es un insert-or-update fijo contra la misma tabla de tareas que usa el resto del modelo de Kanban, indexado por `repo` + `issue_number`, así que reenviar el mismo evento actualiza la tarjeta existente en lugar de duplicarla.

**Configuración**: crea una tarea con `trigger_config: {"provider": "github", "events": ["issues"]}`. La sesión y el workspace de esa tarea se convierten en la base de cada tarjeta derivada de un issue. Sin esa tarea habilitada, los eventos de issue se siguen recibiendo y persistiendo, pero no se crea ninguna tarjeta.

## Modelo 3 — Alertas de observabilidad → Kanban (determinístico, sin LLM)

Mismo mecanismo que el Modelo 2, pero para herramientas de alertas (Sentry, Grafana, PagerDuty, o cualquier cosa que pueda enviar un webhook) vía el endpoint dedicado `/webhook/observability` y su contrato de payload fijo. La severidad determina la columna inicial de la tarjeta — `critical`/`high` cae en triage, el resto en todo. La referencia completa de campos, detalles de autenticación y configuración por proveedor (Sentry, Grafana, PagerDuty) está en [Webhooks de Observabilidad](../observability-webhooks).

## Ver también

- [Webhooks de Observabilidad](../observability-webhooks) — contrato completo de payload y configuración de proveedores para el Modelo 3
- [Automatización del Agente](../agent-automation) — programación, delegación y Vectora Connect
- [Usando el Workbench](../using-the-workbench) — la pestaña Tasks donde aparecen estas ejecuciones

---
title: Plan Mode, AgentRun y decisiones
weight: 5
---

Esta guía explica cómo planificar una tarea antes de ejecutarla, cómo seguir cada ejecución del agente con AgentRun y cómo quedan registradas las decisiones de aprobación.

> **Estado:** estas capacidades forman parte de la línea 0.3. La configuración de Decision Models depende de que el PR #404 se integre en `release/0.3`. Esta página no declara concluidas las pruebas live que todavía aparecen como `skipped`.

## Requisitos previos

- Un workspace abierto en Vectora.
- Un proveedor de modelo configurado para el chat.
- Para Decision Models: un proveedor de decisión configurado (consulta [Modelos de decisión](../decision-models)).

## Configuración

### Plan Mode

Plan Mode se usa de dos formas:

- **Prefijo `/plan`:** un mensaje que empieza con `/plan` se envía en modo de planificación. El prefijo se elimina antes de que el texto llegue al agente.
- **Modo de permiso `Plan`:** el agente se detiene en la primera acción protegida del turno y pide tu aprobación. Después de aprobar, el plan continúa en el mismo turno. La aprobación no vale para turnos siguientes.

Plan Mode no es la pestaña **Plan** del workbench. La pestaña muestra los artifacts y la checklist de la sesión; Plan Mode controla cuándo se detiene el agente. Consulta [Usar el chat](../using-the-chat) para ver todos los modos de permiso.

### AgentRun

Cada ejecución del agente tiene un `run_id`, un estado, evidencias y las relaciones entre el agente principal y los subagentes. El historial del thread se deriva de un ledger de eventos idempotente.

Los estados posibles son:

| Estado | Significado |
| --- | --- |
| `planned`, `admitted` | La ejecución se creó y se aceptó, pero aún no empezó |
| `running`, `tool_running` | El agente está generando o ejecutando una herramienta |
| `waiting_approval` | Esperando una decisión humana |
| `waiting_provider` | Esperando al proveedor del modelo |
| `verifying` | Ejecutando los quality gates del workspace |
| `passed`, `degraded`, `failed`, `cancelled` | Estados finales |
| `recovery_required` | La ejecución se interrumpió y necesita reconciliación antes de continuar |

Estas rutas requieren autenticación:

- `GET /runs/{run_id}`: estado y evidencias de una ejecución.
- `GET /runs/{run_id}/timeline`: línea de tiempo de eventos de la ejecución.
- `GET /threads/{thread_id}/runs`: ejecuciones de un thread.

En modo local, las ejecuciones se guardan en SQLite. Las instalaciones con PostgreSQL siguen el mismo contrato.

### Decisiones registradas

#### Aprobaciones humanas (HITL)

Cuando el agente se detiene, respondes con una de estas decisiones:

| Decisión | Efecto |
| --- | --- |
| `approve` | Ejecuta la acción propuesta |
| `reject` | No la ejecuta; el agente recibe el rechazo como resultado |
| `option:<valor>` | Elige una de las opciones presentadas |
| `edit:<json>` | La ejecuta con los argumentos editados |

La decisión queda asociada al `run_id` de la ejecución.

#### Acceso a servidores MCP

Antes de usar un servidor MCP, Vectora evalúa la política de MCP y registra el resultado:

| Código | Significado |
| --- | --- |
| `allowed` | El servidor está permitido (o no hay regla) |
| `blocked` | El servidor no está en la allowlist aplicable |
| `policy_unavailable` | No se pudo cargar la política; el acceso se niega |

La regla del workspace tiene prioridad sobre la regla de la instancia. La allowlist de MCP controla qué servidores se pueden usar; no es una política de egress (consulta [Políticas de egress](../egress-policies)).

#### Decision Models

Con la capa configurable de HITL, un Decision Model puede evaluar una acción y devolver `allow`, `deny` o `requires_human`. Una decisión nunca amplía permisos: la policy, los gates y los controles locales siguen vigentes. Consulta [Modelos de decisión](../decision-models).

## Limitaciones

- Una aprobación del modo `Plan` solo vale para el turno en que se dio.
- Operaciones como el control del escritorio pueden requerir confirmación en cualquier modo de permiso.
- Una ejecución en `recovery_required` no repite automáticamente herramientas que ya pueden haber producido efectos.
- Los Decision Models necesitan un proveedor configurado y listo; un proveedor no disponible no cuenta como aprobación.

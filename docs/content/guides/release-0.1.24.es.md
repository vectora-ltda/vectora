---
title: Novedades de la versión 0.1.24
weight: 1
---

La versión 0.1.24 reúne la evolución de Vectora: de un workspace con herramientas pasa a un runtime de agentes con ejecuciones identificables, evidencias, planificación, policies y evaluación. Esta página describe las capacidades incluidas en esa versión.

## Novedades

Las secciones siguientes agrupan las capacidades de la línea por área, con los límites que se aplican a cada una.

### Ejecuciones persistentes e historial

AgentRun asocia estado del ciclo de vida, `run_id`, evidencias y relaciones entre el agente principal y los subagentes. El ledger de eventos permite reconstruir el historial de la conversación. El almacenamiento local usa SQLite; las instalaciones distribuidas pueden configurar PostgreSQL. Los checkpoints y la reanudación dependen de la ruta de ejecución y del almacenamiento configurado.

Consulta [sesiones y workspaces](../../concepts/sessions-and-workspaces) y la [referencia de agentes](../../reference/agents).

### Planes, subagentes y Kanban

Plan Mode puede dividir una solicitud en etapas ejecutables y dependencias. Kanban organiza tareas en segundo plano y sigue sus ejecuciones; las policies y los gates determinan qué requiere aprobación. La delegación paralela funciona mejor con tareas independientes y perfiles limitados a sus herramientas permitidas.

Consulta [subagentes](../../concepts/sub-agents), [automatización de agentes](../agent-automation) y [uso del chat](../using-the-chat). La guía [Plan Mode, AgentRun y decisiones](../plan-mode-and-agentrun) detalla Plan Mode, los estados de AgentRun y las decisiones registradas.

### HITL y Decision Models configurables


Para configurarlo:

1. Abre **Configuración → Modelos de IA** y selecciona un proveedor de decisiones.
2. Configura las credenciales y comprueba el estado de disponibilidad del runtime.
3. Habilita solo las superficies donde se puede consultar el modelo.
4. En el chat, elige si usar el modelo configurado en esa ejecución.
5. Recuerda que las decisiones del modelo no sustituyen las policies, permisos ni gates de aprobación.

Si una evaluación se inicia y falla, la acción no debe quedar autorizada. Un modelo ausente o no disponible no cuenta como decisión aprobada; los controles estáticos siguen activos. Consulta [Decision Models](../decision-models) y [configuración](../using-settings).

### Verificación y Evaluation Lab

Los quality gates evalúan resultados después de la ejecución y distinguen aprobado, fallido, bloqueado, degradado y no verificado. La CLI no captura interacciones live directamente. Activa `EVALUATION_RECORDING_ENABLED=true` antes de ejecutar para registrar las interacciones del modelo en AgentRun. Guarda el JSON de la respuesta autenticada de `GET /runs/{run_id}` y usa `vectora eval export-run --run-file run.json --target cassette.json`; el comando usa `cassette_from_run` y rechaza ejecuciones sin interacciones grabadas. Eliminar la sesión borra su trace almacenado, pero los archivos exportados requieren retención y eliminación separadas. Solo se ocultan campos y patrones conocidos de credenciales; los mensajes y respuestas pueden conservar código privado o datos personales. Restringe el acceso y revisa los cassettes antes de compartirlos.

Usa `vectora eval run` para reproducir un único cassette y `vectora eval compare` para comparar resultados. Replay valida un cassette reproducible, no la disponibilidad live del modelo, Browser Workbench o un servicio externo. Consulta [Verificación y Evaluation Lab](../verification-lab) para configurar los perfiles de quality gates.

### Memoria, contexto y RAG

Context Planner y Context Graph organizan el contexto del proyecto; la memoria persistente y los buckets separan conjuntos de conocimiento. La ingesta indexa documentos para recuperación vectorial y, según el modo de almacenamiento y los proveedores configurados, recuperación híbrida y reranking.

Añade documentos al workspace, espera a que se procese la cola de embeddings y revisa los resultados antes de depender del índice. Consulta [RAG](../../concepts/rag), [Context Graph](../../concepts/context-graph) y [almacenamiento](../../concepts/storage).

### Browser Workbench y policy/egress

La navegación del Browser Workbench usa Chromium de Electron cuando están disponibles el sidecar y la sesión del workspace preparada por la UI. En la ruta predeterminada Tavily, `fetch_url` usa Chromium como fallback sin clave de API o tras errores de cuota/rate limit. `web_search` envía consultas al proveedor configurado (por ejemplo Tavily) y puede recurrir a la API de DuckDuckGo. `web_crawl` y `web_map` requieren `TAVILY_API_KEY` y no tienen fallback Chromium. Fetch, crawl y map comprueban la URL inicial con la política de egress; Search no aplica esa política de destino. El fallback Chromium no revalida redirecciones ni fija la IP de destino.

Los snapshots de policy y las comprobaciones de egress se aplican a las rutas de herramientas conectadas. La cobertura depende de cada adapter; una integración no conectada no hereda controles por aparecer en esta página. Consulta [Políticas de egress](../egress-policies), [workbench](../using-the-workbench) y [seguridad](../../security).

## Validar el entorno

- Comprueba la versión instalada y la disponibilidad de cada proveedor antes de iniciar una ejecución.
- Empieza con una ejecución sencilla en el chat y luego prueba aprobación, bloqueo y error con una acción controlada.
- Para proveedores locales, verifica que el servicio y los modelos necesarios estén disponibles.
- Distingue los resultados `passed`, `failed` y `skipped`. Una prueba `skipped` no valida la ruta live.
- No uses replay como sustituto de una llamada real cuando el objetivo sea validar la integración live.

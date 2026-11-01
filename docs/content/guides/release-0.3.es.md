---
title: Novedades de la línea 0.3
weight: 1
---

La línea `release/0.3` amplía Vectora: de un workspace con herramientas pasa a un runtime de agentes con ejecuciones identificables, evidencias, planificación, policies y evaluación. Esta página describe la composición de la línea, incluido el PR #404 apilado sobre ella. La etiqueta `0.3.0` aún debe publicarse para que este contenido represente una release distribuida.

## Novedades

Las secciones siguientes agrupan las capacidades de la línea por área, con los límites que se aplican a cada una.

### Ejecuciones persistentes e historial

AgentRun asocia estado del ciclo de vida, `run_id`, evidencias y relaciones entre el agente principal y los subagentes. El ledger de eventos permite reconstruir el historial de la conversación. El almacenamiento local usa SQLite; las instalaciones distribuidas pueden configurar PostgreSQL. Los checkpoints y la reanudación dependen de la ruta de ejecución y del almacenamiento configurado.

Consulta [sesiones y workspaces](../../concepts/sessions-and-workspaces) y la [referencia de agentes](../../reference/agents).

### Planes, subagentes y Kanban

Plan Mode puede dividir una solicitud en etapas ejecutables y dependencias. Kanban organiza tareas en segundo plano y sigue sus ejecuciones; las policies y los gates determinan qué requiere aprobación. La delegación paralela funciona mejor con tareas independientes y perfiles limitados a sus herramientas permitidas.

Consulta [subagentes](../../concepts/sub-agents), [automatización de agentes](../agent-automation) y [uso del chat](../using-the-chat). La guía [Plan Mode, AgentRun y decisiones](../plan-mode-and-agentrun) detalla Plan Mode, los estados de AgentRun y las decisiones registradas.

### HITL y Decision Models configurables

En el PR #404, **Configuración → Modelos de IA** reúne proveedores de IA y las superficies autorizadas para consultar Decision Models. El chat incluye un menú de Decision Model y un panel HITL. Clef, Laya, JEV u otro proveedor compatible pueden devolver una decisión estructurada `allow`, `deny` o `requires_human`.

Para configurarlo:

1. Abre **Configuración → Modelos de IA** y selecciona un proveedor de decisiones.
2. Configura las credenciales y comprueba el estado de disponibilidad del runtime.
3. Habilita solo las superficies donde se puede consultar el modelo.
4. En el chat, elige si usar el modelo configurado en esa ejecución.
5. Recuerda que las decisiones del modelo no sustituyen las policies, permisos ni gates de aprobación.

Si una evaluación se inicia y falla, la acción no debe quedar autorizada. Un modelo ausente o no disponible no cuenta como decisión aprobada; los controles estáticos siguen activos. Consulta [Decision Models](../decision-models) y [configuración](../using-settings).

### Verificación y Evaluation Lab

Los quality gates evalúan resultados después de la ejecución y distinguen aprobado, fallido, bloqueado, degradado y no verificado. Un cassette guarda las interacciones de una ejecución, con los datos sensibles ocultos al guardarlo; replay permite comparar un cassette existente sin volver a llamar al proveedor externo. La CLI no tiene un comando para grabar cassettes.

Usa `vectora eval run` para reproducir un único cassette y `vectora eval compare` para comparar resultados. Replay valida un cassette reproducible, no la disponibilidad live del modelo, Browser Workbench o un servicio externo. Consulta [Verificación y Evaluation Lab](../verification-lab) para configurar los perfiles de quality gates.

### Memoria, contexto y RAG

Context Planner y Context Graph organizan el contexto del proyecto; la memoria persistente y los buckets separan conjuntos de conocimiento. La ingesta indexa documentos para recuperación vectorial y, según el modo de almacenamiento y los proveedores configurados, recuperación híbrida y reranking.

Añade documentos al workspace, espera a que se procese la cola de embeddings y revisa los resultados antes de depender del índice. Consulta [RAG](../../concepts/rag), [Context Graph](../../concepts/context-graph) y [almacenamiento](../../concepts/storage).

### Browser Workbench y policy/egress

Cuando están activos el sidecar Electron y la sesión preparada por la interfaz, el runtime usa Chromium integrado para buscar y navegar. Fetch, crawl y map comprueban la URL de destino con la política de egress; la búsqueda envía una consulta, y las peticiones del proveedor y del fallback no pasan por esa política; los límites de páginas, profundidad y origen restringen la recopilación. El fallback HTTP no es la misma sesión del navegador ni hereda sus cookies.

Los snapshots de policy y las comprobaciones de egress se aplican a las rutas de herramientas conectadas. La cobertura depende de cada adapter; una integración no conectada no hereda controles por aparecer en esta página. Consulta [Políticas de egress](../egress-policies), [workbench](../using-the-workbench) y [seguridad](../../security).

## Validar el entorno

- Comprueba la versión instalada y la disponibilidad de cada proveedor antes de iniciar una ejecución.
- Empieza con una ejecución sencilla en el chat y luego prueba aprobación, bloqueo y error con una acción controlada.
- Para proveedores locales, verifica que el servicio y los modelos necesarios estén disponibles.
- Distingue los resultados `passed`, `failed` y `skipped`. Una prueba `skipped` no valida la ruta live.
- No uses replay como sustituto de una llamada real cuando el objetivo sea validar la integración live.

Los PR [#382](https://github.com/vectora-ltda/vectora/pull/382), [#400](https://github.com/vectora-ltda/vectora/pull/400), [#404](https://github.com/vectora-ltda/vectora/pull/404), [#408](https://github.com/vectora-ltda/vectora/pull/408) y [#414](https://github.com/vectora-ltda/vectora/pull/414) describen la composición y los complementos de la línea. Las etiquetas y artefactos publicados son la referencia final para saber qué está disponible.

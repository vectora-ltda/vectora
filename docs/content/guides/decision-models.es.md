---
title: Modelos de decisión
weight: 2
---

Los Decision Models evalúan decisiones estructuradas del runtime, como el riesgo de una llamada a una herramienta. No sustituyen los permisos de las herramientas, la policy del workspace ni las etapas de aprobación.

## Configuración

En **Configuración → Modelos de IA**, selecciona un provider disponible, configura las credenciales necesarias y habilita Decision Models. La matriz de superficies controla dónde se puede consultar el modelo, por ejemplo HITL, subagentes, Plan Mode, Browser, MCP, terminal, red y tareas en segundo plano.

Las preferencias se asocian al usuario y al workspace activo. Al cambiar de workspace, vuelve a comprobar el provider y las superficies habilitadas. Una superficie desactivada no debe quedar autorizada por la preferencia de otra superficie.

## Cómo afecta una decisión a una acción

1. El runtime comprueba la configuración y la superficie aplicable.
2. Si la evaluación está habilitada y el provider está listo, el modelo devuelve una decisión estructurada.
3. La policy y los controles locales siguen teniendo autoridad. El modelo puede exigir más aprobación o bloquear una acción; su respuesta no concede permisos que la policy haya denegado.
4. Si una evaluación ya iniciada supera el plazo, falla, devuelve contenido inválido o requiere revisión humana, la acción no se considera aprobada.
5. Si Decision Model está deshabilitado o no está disponible antes de iniciar la evaluación, no se presume una decisión automatizada adicional; los gates estáticos del runtime siguen vigentes.

## Límites

- Habilitar la función no hace que todos los providers estén disponibles. Las credenciales, el estado de salud, las dependencias locales y la conectividad deben funcionar en el entorno actual.
- Una opción seleccionada en la interfaz expresa una preferencia. No demuestra que el provider haya respondido ni que se haya ejecutado una evaluación.
- Las pruebas unitarias y de integración no demuestran la disponibilidad live de Clef, Laya o JEV. Los resultados `skipped` deben seguir identificados como no ejecutados.
- Decision Models no sustituyen la confirmación humana cuando la policy o el resultado exigen aprobación.

## Seguridad y datos

Las decisiones se asocian al contexto de ejecución. No incluyas credenciales ni contenido privado en los campos de decisión. La interfaz debe ocultar los secretos y los registros de auditoría deben guardar solo los metadatos y resúmenes necesarios para explicar la decisión.

## Cambios durante una ejecución

Las preferencias de decisión se registran por ejecución. Cambiar el proveedor, activar o desactivar el modelo o cambiar sus superficies invalida la autorización anterior. Volver a la configuración original no recupera esa autorización: se requiere una nueva evaluación o aprobación según la política. La autorización se comprueba nuevamente antes de invocar la herramienta, incluso después de la preparación asíncrona.

## Transcripción local

La transcripción y las pruebas del runtime comparten un límite de una operación nativa por proceso. Espere a que termine una operación en curso antes de reintentar. La transcripción espera hasta 60 segundos; la prueba, hasta 45 segundos. Un timeout no termina la inferencia nativa: el cupo permanece ocupado y el audio temporal se elimina cuando termina el procesamiento. Un resultado tardío nunca se presenta como una prueba exitosa después de un timeout.

## Grabación y replay de una ejecución

Active `EVALUATION_RECORDING_ENABLED=true` antes de iniciar el backend para capturar las entradas reales del modelo. La grabación es opcional y está desactivada por defecto; guarda el contexto y las respuestas en AgentRun con redacción de credenciales. El contenido aún puede incluir información privada: aplique los mismos controles de acceso y retención que al historial.

Guarde como `run.json` la respuesta JSON de la consulta autenticada `GET /runs/{run_id}`. Exporte y reproduzca la cassete:

```shell
vectora eval export-run --run-file run.json --target run.cassette.json
vectora eval run --suite run.cassette.json --output json
```

La exportación vincula las interacciones con las decisiones y los digests de resultados observados de herramientas. Las operaciones pendientes se identifican como resultados incompletos. El replay usa respuestas grabadas sin llamar a proveedores ni volver a ejecutar herramientas. No demuestra disponibilidad live ni reproducibilidad del entorno externo.

La pestaña **Modelos de IA** también incluye el selector del modelo principal del chat y una sección dedicada a comprensión de imágenes y respaldo de visión. El orden de respaldo del chat permanece separado de la generación de imágenes, TTS y vídeo.

Las grabaciones de evaluación se guardan en AgentRun y se eliminan con la sesión. Los archivos JSON exportados son copias independientes: aplica la política de retención de tu entorno y elimínalos por separado cuando ya no sean necesarios.

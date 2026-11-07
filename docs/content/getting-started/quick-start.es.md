---
title: Inicio rápido
weight: 3
---

Esta guía cubre el primer uso después de la instalación. Para compilar el proyecto, consulta [Instalación](../installation).

## 1. Inicia Vectora y configura un modelo

Abre la aplicación. En una instancia local nueva, crea la cuenta inicial y sigue el asistente de configuración. Después, abre **Configuración → Modelos de IA** para elegir los modelos predeterminados y configurar los proveedores necesarios. Usa **Provider Routing** para endpoints y enrutamiento.

Configura solo las capacidades que quieras usar. Un proveedor remoto puede recibir las solicitudes y los datos necesarios para una tarea; los modelos locales deben estar instalados y ejecutándose en el entorno del backend. Consulta [Configuración](../../guides/using-settings) y [BYOK y privacidad](../../security/byok-privacy).

## 2. Abre un workspace

Usa el selector de workspace para agregar la carpeta del proyecto. El workspace delimita el contexto de archivos, terminal, Git, búsqueda y herramientas del agente.

Un workspace nuevo empieza como no confiable. Lee el aviso y confía en él solo si conoces su origen y aceptas que el agente ejecute las operaciones permitidas por la configuración. Consulta [Primer workspace](../first-workspace).

## 3. Prueba una solicitud pequeña

Empieza con una pregunta cuya respuesta puedas verificar:

```text
Resume la estructura de este proyecto e indica qué archivos usaste como evidencia.
```

Después, prueba un cambio pequeño en una rama de trabajo. El agente puede usar herramientas o delegar tareas; los permisos, las políticas y los gates HITL determinan si una acción requiere confirmación.

## 4. Explora la recuperación del proyecto

Abre la pestaña **Memory** de la workbench para consultar la base de conocimiento y los resultados recuperados. La indexación depende de los archivos aceptados y de los proveedores configurados; comprueba el resultado antes de asumir que se indexó una carpeta completa. [RAG y Context Graph](../../concepts/rag) explica las fuentes de contexto y sus límites.

## Siguientes pasos

- [Usar el chat](../../guides/using-the-chat)
- [Usar la configuración](../../guides/using-settings)
- [Decision Models](../../guides/decision-models)
- [Solución de problemas](../troubleshooting)

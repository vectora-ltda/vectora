---
title: RAG y contexto del proyecto
weight: 1
---

RAG (Retrieval-Augmented Generation) permite que el agente recupere fragmentos de documentos indexados para responder con contexto del workspace. La recuperación depende de los archivos incluidos, el estado de la cola de indexación, el almacenamiento seleccionado y los proveedores de embeddings/reranking configurados.

## Flujo general

```text
archivos y documentos seleccionados
             │
             ▼
       extracción y división
             │
             ▼
       embeddings e índice
             │
             ▼
 recuperación de fragmentos relevantes
             │
             ▼
 contexto enviado al modelo elegido
```

Vectora combina recuperación léxica y vectorial cuando esas funciones están disponibles en la configuración. El reranking es configurable y puede usar un proveedor compatible. La búsqueda web es una herramienta independiente y no debe confundirse con la recuperación local. Un proveedor remoto puede recibir la consulta y los fragmentos necesarios; revisa su política de datos antes de usarlo con contenido confidencial.

## Embeddings y reranking

Elige proveedores y modelos en **Configuración → Entorno → Modelos de IA**; configura endpoints y credenciales en **Provider Routing**. Las opciones incluyen proveedores remotos compatibles y configuraciones locales. Vectora no exige una clave Cohere para iniciar el chat. La indexación necesita un proveedor de embeddings funcional, y el reranking puede configurarse por separado o desactivarse.

## Consultar el índice

Usa la pestaña **Memory** del workbench para seguir la actividad de recuperación y ajustar las opciones RAG disponibles. Incluye los archivos necesarios en el workspace, espera a que termine la indexación y revisa las fuentes recuperadas antes de asumir que se indexó todo el proyecto.

## Límites

RAG recupera fragmentos de un índice; no garantiza integridad, actualidad ni exactitud. Los archivos excluidos, errores de extracción, límites de contexto y ajustes del proveedor afectan los resultados. Verifica las afirmaciones importantes en la fuente original.

## Ver también

- [Context Graph](../context-graph) — relaciones estructurales extraídas del código
- [Usando el workbench](../../guides/using-the-workbench) — el panel Memory en la práctica
- [BYOK y privacidad](../../security/byok-privacy) — flujo de datos hacia proveedores externos

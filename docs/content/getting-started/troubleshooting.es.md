---
title: Solución de problemas
weight: 6
---

## El selector de modelos está vacío o el chat no inicia

Configura un proveedor de chat y un modelo disponibles en **Configuración → Entorno → Modelos de IA**. Abre **Provider Routing** para configurar credenciales y endpoints. Los nombres de las pestañas pueden variar según la versión; consulta también [Configuración de modelos](../../guides/using-settings). No necesitas configurar embeddings, reranking ni búsqueda web para iniciar una conversación normal.

## La búsqueda del proyecto o la indexación no devuelve resultados

Confirma que el workspace y sus archivos estén incluidos en la indexación y que el proveedor de embeddings configurado esté disponible. Revisa la pestaña **Memory** del workbench y los logs del backend para encontrar fallos de indexación, embeddings o reranking. Estas funciones pueden usar proveedores distintos al del chat. Consulta [RAG](../../concepts/rag) y [Almacenamiento](../../concepts/storage).

## El agente no puede escribir archivos ni ejecutar comandos

Comprueba si el workspace es de confianza y si el modo de permisos, la política de herramientas y el sandbox permiten la operación. El acceso a herramientas puede requerir aprobación HITL. No marques como confiable una carpeta cuyo origen desconoces. Consulta [Primer workspace](../first-workspace) y [Seguridad](../../security/).

## No aparecen herramientas de un servidor MCP

Revisa el estado de la conexión en **Configuración → Entorno → Plugins**. Comprueba el comando, los argumentos, el transporte y los logs del conector. Consulta la [referencia del cliente MCP](../../reference/mcp-client).

## El modo de almacenamiento completo no conecta

El modo `complete` necesita que PostgreSQL, Qdrant y Redis estén disponibles en las URLs configuradas (`POSTGRES_DSN`, `QDRANT_URL`, `REDIS_URL`). Para desarrollar desde el código fuente, consulta el comando `scons docker` en la [guía de Docker](../../deployment/docker). El modo local `lite` no necesita estos servicios.

## Obtener más detalles

Los logs locales se guardan en `~/.vectora/logs/`. Elimina o enmascara tokens, rutas privadas y contenido de conversaciones antes de compartir un fragmento.

## Informar de un problema

Busca primero en las [issues existentes](https://github.com/vectora-company/vectora/issues). Para una vulnerabilidad, sigue la [política de seguridad](https://github.com/vectora-company/vectora/blob/master/SECURITY.md) en lugar de publicar detalles en una issue.

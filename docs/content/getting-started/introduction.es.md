---
title: Introduction
weight: 1
---

## Qué es Vectora

Vectora es un **workspace de IA autoalojado** para equipos de desarrollo. Se ejecuta enteramente en tu servidor — tu VPS, tu máquina local, el servidor de tu empresa — y resuelve dos problemas que la mayoría de los asistentes de IA ignoran: darte a ti y al agente las **mismas superficies de trabajo** (filesystem, terminal, git, navegador — no un chat que narra acciones que nunca ves), y **hacer que el agente realmente conozca tu proyecto**, no solo generar código genérico.

Esto ocurre a través de dos caminos complementarios:

- **RAG híbrido** — indexa código, documentación y decisiones pasadas con búsqueda por palabras clave (BM25) + búsqueda vectorial densa + reranking, de modo que el agente responde según lo indexado, no según lo que "cree" que es cierto.
- **Context Graph nativo** — un grafo de conocimiento del workspace (funciones, clases, conceptos y cómo se relacionan), construido vía parsing AST (tree-sitter) y extracción semántica por LLM. Complementa el RAG con contexto estructural que los embeddings solos no pueden capturar.

## Arquitectura en una imagen

```text
Tú (CLI / Chat web / cliente MCP)
        │
        ▼
   Orquestador (motor nativo — loop imperativo)
        │
   ┌────┴────┐
   ▼         ▼
 coder     search      ← sub-agentes especializados
   │         │
   └────┬────┘
        ▼
  Herramientas nativas (archivos, git, terminal, navegador, RAG, web, integraciones)
        │
        ▼
  SQLite + LanceDB (lite, por defecto)  o  Postgres + Qdrant + Redis (completo)
```

El **orquestador** es el supervisor: responde directamente para preguntas simples, o delega a un sub-agente especializado (`coder` para operaciones de archivos/git/terminal, `search` para búsqueda web y RAG) a través de middleware human-in-the-loop (HITL) que pausa antes de acciones destructivas para tu aprobación.

## Tres formas de usarlo

El mismo backend sirve tres superficies distintas, al mismo tiempo:

1. **Chat web** — interfaz React para uso local individual y, con una suscripción Pro, operación con varios miembros; su workbench (archivos, git, terminal, navegador, RAG, Context Graph, Kanban) se comparte entre tú y el agente — ver [Usando el chat](../guides/using-the-chat) y [Usando el workbench](../guides/using-the-workbench).
2. **CLI** — `vectora start`, `vectora config`, `vectora storage` — ver la [referencia de la CLI](../reference/cli).
3. **Cliente MCP** — Vectora se conecta a servidores MCP que instales (un marketplace de conectores, más registro manual) para que el agente use sus herramientas. Vectora no se expone a sí mismo como servidor MCP para otros harnesses — ver [Cliente MCP](../reference/mcp-client).

## Código abierto y operación

Vectora se distribuye bajo la licencia [AGPL-3.0](https://github.com/vectora-company/vectora/blob/master/LICENSE). Puedes ejecutarlo en tu equipo o infraestructura y estudiar, modificar y redistribuir el código según los términos de la licencia. La licencia no significa que todas las funciones estén validadas para producción ni que los servicios externos sean gratuitos.

El almacenamiento local usa SQLite y LanceDB. El operador configura los proveedores de IA y las integraciones externas; los costes, la retención y el uso de los datos enviados dependen de las políticas de cada servicio conectado. Las funciones que dependen de Vectora Services pueden requerir una cuenta o una suscripción activa. Consulta el [repositorio](https://github.com/vectora-company/vectora), la [guía de contribución](https://github.com/vectora-company/vectora/blob/master/CONTRIBUTING.md) y las [releases](https://github.com/vectora-company/vectora/releases) para obtener información verificable.

## Suscripciones Free y Pro

La licencia de código abierto y la suscripción comercial son cosas distintas. El uso local individual de la aplicación y la CLI es gratuito y no requiere cuenta. Las funciones centrales del producto —incluidos RAG, Library, Browser, Sandbox, Context Graph, HITL y Decision Models— no están bloqueadas por la suscripción Pro. Los proveedores externos de IA pueden cobrar por su propio uso.

**Free** incluye ejecución local, alojamiento propio y almacenamiento administrado por el operador, como Postgres, Qdrant y Redis. **Pro** habilita funciones alojadas que dependen de Services, como organizaciones e invitaciones, webhooks remotos y Vectora Connect. Consulta las [suscripciones y los precios](https://vectora.company/#pricing).

## Siguiente paso

→ [Instalación](../installation)

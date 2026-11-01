---
title: Documentation
type: docs
cascade:
  type: docs
sidebar:
  open: true
---

Vectora es un **workspace de desarrollo asistido por IA** que se ejecuta en la infraestructura que controlas. Tú y el agente trabajan lado a lado en el mismo filesystem, terminal, Git y navegador; cada usuario configura sus conectores MCP. Los modelos pueden ser locales o remotos: al elegir un proveedor externo, las solicitudes necesarias se envían a ese servicio.

En su núcleo, Vectora cierra la **brecha de conocimiento** entre un LLM y tu código, documentación y stack actuales: un pipeline **RAG** híbrido (BM25 + vectores densos + reranker) para recuperación por similitud, y un **Context Graph** nativo (workspace analizado vía tree-sitter + extracción por LLM) para contexto estructural.

## Por dónde empezar

| Quiero...                                  | Ir a                                             |
| ------------------------------------------- | ------------------------------------------------- |
| Instalar Vectora                            | [Instalación](./getting-started/installation)      |
| Ejecutarlo en 5 minutos                     | [Inicio rápido](./getting-started/quick-start)   |
| Entender el pipeline RAG                    | [RAG y Context Graph](./concepts/rag)             |
| Conectar un servidor MCP (como cliente)     | [Cliente MCP](./reference/mcp-client)             |
| Ver todos los comandos de la CLI            | [Referencia de la CLI](./reference/cli)           |
| Desplegar en un servidor                    | [Requisitos](./deployment/requirements)           |
| Entender auth, secretos y BYOK              | [Seguridad](./security/authentication)            |
| Conocer las novedades de la línea 0.3       | [Novedades de la línea 0.3](./guides/release-0.3) |

## Qué es Vectora (y qué no es)

Vectora es **software comercial de código cerrado** — no es open source. Lo ejecutas en tu propia infraestructura (tu servidor, tu VPS, tu escritorio), pero el código fuente pertenece a Vectora Company. Es el mismo modelo que Cursor, Linear o Notion: la infra es tuya, el código es del proveedor.

- **Free** ejecuta la instancia local sin exigir una cuenta. Los proveedores externos y conectores envían las solicitudes necesarias a los servicios configurados; se pueden usar modelos locales cuando estén disponibles.
- **Pro** es opcional y cubre trial/facturación/licenciamiento vía `services.vectora.company`, un pequeño Cloudflare Worker — no es una "Vectora Cloud" que aloja o ejecuta tu instancia por ti. Actualizar cambia qué funciones están disponibles (stack de almacenamiento de alto rendimiento, chat web multiusuario, automatizaciones disparadas por webhook), nunca dónde se ejecuta el agente.

Consulta la [página de precios](https://vectora.company/#pricing) para los planes actuales.

## Límites de la línea 0.3

- **Ejecución local:** el sandbox se activa solo cuando el workspace no tiene `vectora.toml` y el entorno admite aislamiento nativo; con `vectora.toml`, rige la sección `[sandbox]` (`enabled = false` en la plantilla). El backend `local` funciona en Linux (en Windows, mediante WSL2; en macOS el sandbox no se activa automáticamente). `allow_tcp_ports` requiere Landlock ABI V4 (kernel 6.7+), y una lista vacía no restringe la red. Consulta [Sandbox](./concepts/sandbox) y [Políticas de egress](./guides/egress-policies).
- **Proveedores remotos:** con claves BYOK o endpoints de Ollama, OpenRouter y 9router, las solicitudes salen de la máquina donde corre el backend directamente hacia el proveedor elegido.
- **Validaciones omitidas:** las pruebas live marcadas como `skipped` no demuestran el funcionamiento live; el replay de cassettes no sustituye una prueba con el proveedor real. Consulta [Verificación y Evaluation Lab](./guides/verification-lab).

---
title: General
weight: 1
---

**¿Vectora es open source?**
Sí. El código fuente está bajo la licencia GNU AGPL-3.0. Consulta el archivo [LICENSE](https://github.com/vectora-ltda/vectora/blob/master/LICENSE) para conocer todos los términos.

**¿Necesito una cuenta para ejecutar Vectora localmente?**
El operador puede configurar una instancia local sin usar los servicios opcionales de Vectora. Las funciones que dependen de proveedores externos, autenticación o servicios auxiliares tienen requisitos propios.

**¿Qué LLMs son compatibles?**
Vectora integra proveedores de chat configurables, incluidos Google, OpenAI, Anthropic, Cohere, Ollama y endpoints compatibles. Los modelos disponibles dependen de la configuración y compatibilidad del proveedor.

**¿Puedo usarlo sin internet?**
Parcialmente. Un modelo local puede responder sin enviar la conversación a un proveedor de chat remoto. La búsqueda web, las integraciones remotas y cualquier proveedor externo requieren red; configura embeddings y reranking por separado para RAG.

**¿Cuál es la diferencia entre Vectora y un asistente de código como Copilot?**
Vectora es un entorno autoalojado para trabajar con agentes y proyectos: combina chat con herramientas de archivos, terminal, Git, búsqueda de contexto y workbenches. Las acciones disponibles dependen de los permisos, la configuración y los proveedores conectados.

**¿Hay una versión móvil?**
No hay una aplicación móvil independiente publicada. La interfaz web puede abrirse en un navegador móvil cuando la instancia sea accesible por red, pero eso no significa que el backend de escritorio y todos los sidecars se ejecuten en el teléfono.

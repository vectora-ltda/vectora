---
title: General
weight: 1
---

**¿Vectora es open source?**
Sí. El código fuente es público bajo la licencia GNU AGPL-3.0. Puedes usarlo, estudiarlo, modificarlo y redistribuirlo; si distribuyes una versión modificada o la ofreces como servicio en red, debes publicar su código fuente bajo la misma licencia. Vectora Ltda es titular de los derechos de autor y también ofrece una licencia comercial para quien no pueda cumplir la AGPL — escribe a contato@vectora.company.

**¿Necesito una cuenta para usar Free?**
No. Free se ejecuta 100% localmente, sin registro, sin ninguna dependencia de `services.vectora.company`.

**¿Qué LLMs son compatibles?**
Google Gemini, OpenAI, Anthropic, Cohere y Ollama (totalmente local). El selector de modelos solo muestra los que tienen una key configurada.

**¿Puedo usarlo sin internet?**
Parcialmente. Con Ollama local, el LLM se ejecuta sin conexión — pero el RAG (Cohere/VoyageAI) y la búsqueda web (Tavily) siguen dependiendo de la red, a menos que configures alternativas locales.

**¿Cuál es la diferencia entre Vectora y un asistente de código como Copilot?**
Copilot es autocompletado. Vectora es un agente completo con RAG dedicado, terminal, git, chat web multiusuario y un workspace compartido con el agente — construido para realmente conocer tu proyecto, no solo sugerir la siguiente línea.

**¿Hay una versión móvil?**
No. Vectora es de escritorio (Windows/macOS/Linux) + un chat web accesible desde cualquier navegador (incluido móvil, vía el servidor ejecutándose en un VPS/servidor de la empresa).

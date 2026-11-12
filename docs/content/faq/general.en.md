---
title: General
weight: 1
---

**Is Vectora open source?**
Yes. The source code is licensed under GNU AGPL-3.0. See the [LICENSE](https://github.com/vectora-company/vectora/blob/master/LICENSE) file for the complete terms.

**Do I need an account to run Vectora locally?**
A local instance can be configured by its operator without using optional Vectora services. Features that rely on external providers, authentication, or auxiliary services have their own requirements.

**Which LLMs are supported?**
Vectora integrates configurable chat providers, including Google, OpenAI, Anthropic, Cohere, Ollama, and compatible endpoints. Available models depend on provider configuration and compatibility.

**Can I use it without internet?**
Partially. A local model can answer without sending the conversation to a remote chat provider. Web search, remote integrations, and any external provider still need network access; configure embeddings and reranking separately for RAG.

**What's the difference between Vectora and a coding assistant like Copilot?**
Vectora is a self-hosted environment for working with agents and projects: it combines chat with file, terminal, Git, context-search tools, and workbenches. Available actions depend on permissions, configuration, and connected providers.

**Is there a mobile version?**
There is no independent mobile app release. The web interface can be opened in a mobile browser when the instance is reachable over the network, but this does not mean the desktop backend and all sidecars run on the phone.

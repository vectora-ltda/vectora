---
title: Geral
weight: 1
---

**Vectora é open source?**
Sim. O código-fonte está sob a licença GNU AGPL-3.0. Consulte o arquivo [LICENSE](https://github.com/vectora-company/vectora/blob/master/LICENSE) para os termos completos.

**Preciso de conta para usar o Vectora localmente?**
Uma instância local pode ser configurada pelo operador sem usar os serviços opcionais do Vectora. Recursos que dependem de providers externos, autenticação ou serviços auxiliares têm requisitos próprios.

**Quais LLMs são suportados?**
O Vectora integra providers de chat configuráveis, incluindo Google, OpenAI, Anthropic, Cohere, Ollama e endpoints compatíveis. A lista disponível depende da configuração e da compatibilidade do provider.

**Dá pra usar sem internet?**
Parcialmente. Um modelo local pode responder sem enviar a conversa a um provider de chat remoto. Busca web, integrações remotas e qualquer provider externo ainda precisam de rede; configure embeddings e reranking separadamente para o RAG.

**Qual a diferença entre o Vectora e um coding assistant tipo Copilot?**
Vectora é um ambiente self-hosted para trabalhar com agentes e projetos: combina conversa com ferramentas de arquivos, terminal, Git, busca de contexto e workbenches. As ações disponíveis dependem das permissões, da configuração e dos providers conectados.

**Existe versão mobile?**
Não há um aplicativo móvel independente publicado. A interface web pode ser aberta num navegador móvel quando a instância estiver acessível pela rede, mas isso não significa que o backend desktop e todos os sidecars rodem no telefone.

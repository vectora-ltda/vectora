---
title: Introdução
weight: 1
---

## O que é o Vectora

Vectora é um **workspace de IA self-hosted** para equipes de desenvolvimento. Ele roda inteiramente no seu servidor — sua VPS, sua máquina local, o servidor da sua empresa — e resolve dois problemas que a maioria dos assistentes de IA ignora: te dar, a você e ao agente, as **mesmas superfícies de trabalho** (filesystem, terminal, git, navegador — não um chat narrando ações que você nunca vê), e **fazer o agente conhecer de verdade o seu projeto**, não só gerar código genérico.

Isso acontece por dois caminhos que trabalham juntos:

- **RAG híbrido** — indexação de código, documentos e decisões passadas com busca por palavra-chave (BM25) + busca vetorial densa + reranking, para que o agente responda com base no que foi indexado, não no que ele "acha" que é verdade.
- **Context Graph nativo** — um grafo de conhecimento do workspace (funções, classes, conceitos e como se relacionam), construído via parsing AST (tree-sitter) e extração semântica por LLM. Complementa o RAG com contexto estrutural que embeddings sozinhos não capturam.

## Arquitetura em uma imagem

```text
Você (CLI / Chat Web / MCP client)
        │
        ▼
   Orchestrator (motor nativo — loop imperativo)
        │
   ┌────┴────┐
   ▼         ▼
 coder     search      ← subagentes especializados
   │         │
   └────┬────┘
        ▼
  Ferramentas nativas (arquivos, git, terminal, navegador, RAG, web, integrações)
        │
        ▼
  SQLite + LanceDB (lite, default)  ou  Postgres + Qdrant + Redis (complete)
```

O **orchestrator** é o supervisor: responde direto quando a pergunta é simples, ou delega para um subagente especializado (`coder` para operações de arquivo/git/terminal, `search` para busca web e RAG) via middleware human-in-the-loop (HITL) que pausa antes de ações destrutivas para você aprovar.

## Os três jeitos de usar

O mesmo backend atende três superfícies diferentes, ao mesmo tempo:

1. **Chat web** — interface React para uso local individual e, com uma assinatura Pro, operação com vários membros; a workbench (arquivos, git, terminal, navegador, RAG, Context Graph, Kanban) é compartilhada entre você e o agente — veja [Usando o chat](../guides/using-the-chat) e [Usando a workbench](../guides/using-the-workbench).
2. **CLI** — `vectora start`, `vectora config`, `vectora storage` — veja a [referência de CLI](../reference/cli).
3. **MCP client** — o Vectora se conecta a servidores MCP que você instalar (um marketplace de conectores, mais registro manual) pra que o agente use as tools deles. O Vectora não se expõe como servidor MCP pra outros harnesses — veja [Cliente MCP](../reference/mcp-client).

## Código aberto e operação

O Vectora é distribuído sob a licença [AGPL-3.0](https://github.com/vectora-company/vectora/blob/master/LICENSE). Você pode executar a aplicação na sua máquina ou infraestrutura e estudar, modificar e redistribuir o código conforme os termos da licença. A licença não significa que todos os recursos tenham sido validados para produção ou que serviços externos sejam gratuitos.

O modo de armazenamento local usa SQLite e LanceDB. Providers de IA e integrações externas são configurados pelo operador; custos, retenção e uso dos dados enviados seguem as políticas de cada serviço conectado. Recursos que dependem do Vectora Services podem exigir conta ou uma assinatura ativa. Consulte o [repositório](https://github.com/vectora-company/vectora), o [guia de contribuição](https://github.com/vectora-company/vectora/blob/master/CONTRIBUTING.md) e a [página de releases](https://github.com/vectora-company/vectora/releases) para informações verificáveis.

## Assinaturas Free e Pro

A licença de código aberto e a assinatura comercial são coisas diferentes. O uso local individual do aplicativo e da CLI é gratuito e não exige conta. Os recursos centrais do produto — incluindo RAG, Library, Browser, Sandbox, Context Graph, HITL e Decision Models — não são bloqueados pela assinatura Pro. O uso de provedores externos pode gerar custos cobrados por esses provedores.

A assinatura **Free** inclui execução local, hospedagem própria e storage configurado pelo operador, como Postgres, Qdrant e Redis. A assinatura **Pro** habilita recursos hospedados que dependem do Services, como organizações e convites, webhooks remotos e Vectora Connect. Consulte as [assinaturas e os preços](https://vectora.company/#pricing).

## Próximo passo

→ [Instalação](../installation)

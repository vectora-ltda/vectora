---
title: Documentação
type: docs
cascade:
  type: docs
sidebar:
  open: true
---

Vectora é um **workspace de desenvolvimento assistido por IA**, executado na infraestrutura que você controla. Você e o agente trabalham lado a lado no mesmo filesystem, terminal, Git e navegador; conectores MCP são configurados pelo usuário. O modelo pode ser local ou remoto: quando você escolhe um provider externo, as solicitações necessárias são enviadas a esse serviço.

No seu núcleo, o Vectora fecha o **abismo de conhecimento** entre um LLM e sua base de código, documentação e stack atual: um pipeline de **RAG** híbrido (BM25 + vetores densos + reranker) para recuperação por similaridade, e um **Context Graph** nativo (workspace analisado via tree-sitter + extração por LLM) para contexto estrutural.

## Por onde começar

| Eu quero...                              | Ir para                                        |
| ---------------------------------------- | ---------------------------------------------- |
| Instalar o Vectora                       | [Instalação](./getting-started/installation)   |
| Rodar em 5 minutos                       | [Início rápido](./getting-started/quick-start) |
| Entender o pipeline de RAG               | [RAG & Context Graph](./concepts/rag)          |
| Conectar um servidor MCP (como cliente)  | [Cliente MCP](./reference/mcp-client)          |
| Ver todos os comandos da CLI             | [Referência de CLI](./reference/cli)           |
| Fazer deploy em um servidor              | [Requisitos](./deployment/requirements)        |
| Entender auth, secrets e BYOK            | [Segurança](./security/authentication)         |
| Conhecer as novidades da linha 0.3       | [Novidades da linha 0.3](./guides/release-0.3) |

## O que o Vectora é (e não é)

Vectora é **software comercial, código fechado** — não é open source. Você roda na sua própria infraestrutura (seu servidor, sua VPS, seu desktop), mas o código-fonte pertence à Vectora Company. É o mesmo modelo do Cursor, Linear ou Notion: a infra é sua, o código é do fornecedor.

- **Free** executa a instância local sem exigir conta. Providers externos e conectores enviam as solicitações necessárias aos serviços configurados; modelos locais podem ser usados quando disponíveis.
- **Pro** é opcional e cobre trial/billing/licenciamento via `services.vectora.company`, um Worker Cloudflare pequeno — não é um "Vectora Cloud" que hospeda ou executa sua instância por você. Fazer upgrade muda quais recursos ficam disponíveis (stack de storage de alto desempenho, chat web multi-usuário, automações disparadas por webhook), nunca onde o agente roda.

Veja a [página de preços](https://vectora.company/#pricing) para os planos atuais.

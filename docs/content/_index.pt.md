---
title: Documentação do Vectora
type: docs
cascade:
  type: docs
sidebar:
  open: true
---

O **Vectora** é um workspace de desenvolvimento assistido por agentes de IA. O aplicativo reúne chat, arquivos, terminal, Git, navegador, busca no projeto e ferramentas de automação; o usuário mantém controle sobre o workspace, as permissões e os provedores conectados.

O aplicativo Vectora é **open source sob AGPL-3.0**. Consulte a [licença](https://github.com/vectora-ltda/vectora/blob/master/LICENSE), o [repositório](https://github.com/vectora-ltda/vectora), as [diretrizes para contribuir](https://github.com/vectora-ltda/vectora/blob/master/CONTRIBUTING.md) e as [issues públicas](https://github.com/vectora-ltda/vectora/issues). O uso local individual é gratuito; uma assinatura Pro habilita recursos e serviços pagos. Provedores externos podem cobrar pelo próprio uso. A assinatura não altera a licença do código.

## Comece por aqui

| Eu quero...                              | Ir para                                        |
| ---------------------------------------- | ---------------------------------------------- |
| Instalar o Vectora                       | [Instalação](./getting-started/installation)   |
| Rodar em 5 minutos                       | [Início rápido](./getting-started/quick-start) |
| Entender o pipeline de RAG               | [RAG & Context Graph](./concepts/rag)          |
| Conectar um servidor MCP (como cliente)  | [Cliente MCP](./reference/mcp-client)          |
| Ver todos os comandos da CLI             | [Referência de CLI](./reference/cli)           |
| Fazer deploy em um servidor              | [Requisitos](./deployment/requirements)        |
| Entender auth, secrets e BYOK            | [Segurança](./security/authentication)         |
| Conhecer as novidades da versão 0.1.24    | [Novidades da versão 0.1.24](./guides/release-0.1.24) |

## Como os dados e modelos funcionam

O Vectora pode ser executado localmente ou em uma máquina que você administra. Modelos locais podem manter as inferências no seu ambiente; ao configurar um provider remoto, as solicitações necessárias são enviadas a esse provider. Antes de conectar serviços, leia [BYOK e privacidade](./security/byok-privacy) e confira quais dados cada integração recebe.

No modo padrão, SQLite e LanceDB mantêm os dados e índices localmente. A configuração `complete` conecta serviços externos de storage que você administra. Consulte [RAG e Context Graph](./concepts/rag), [storage](./concepts/storage) e [requisitos de implantação](./deployment/requirements).

## Sobre esta documentação

Este site contém guias públicos de instalação, configuração, uso, segurança e referência. A documentação acompanha a linha estável `0.1.x`; consulte o [changelog do repositório](https://github.com/vectora-ltda/vectora/blob/master/CHANGELOG.md) para a lista completa de mudanças da versão. Os recursos podem depender de configuração, sistema operacional, provider ou serviço externo. Um teste automatizado ignorado (`skipped`) não comprova disponibilidade operacional.

## Limites da versão 0.1.24

- **Sandbox:** o isolamento depende do sistema operacional, do worker disponível e da configuração do workspace. No Windows é usado WSL2; se o `bwrap` necessário não estiver disponível, o fallback não oferece isolamento de filesystem nem de rede. Leia [Sandbox](./concepts/sandbox) antes de confiar execução automatizada.
- **Providers externos:** chamadas a LLMs, embeddings, rerankers e busca podem enviar dados ao serviço selecionado. A configuração local não significa que um provider remoto processe dados localmente.
- **Validação:** testes de unidade, replay de cassetes e testes live ignorados verificam escopos diferentes. Consulte [Verificação e Evaluation Lab](./guides/verification-lab) antes de interpretar resultados.

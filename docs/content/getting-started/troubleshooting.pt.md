---
title: Solução de problemas
weight: 6
---

## O seletor de modelos está vazio ou o chat falha ao iniciar

Configure um provider de chat e um modelo disponíveis em **Configurações → Ambiente → Modelos de IA**. Para credenciais e endpoints dos providers, abra **Provider Routing**. O nome das abas pode variar conforme a versão; confira também [Configuração de modelos](../../guides/using-settings). Não é necessário configurar embeddings, reranking ou busca web para iniciar uma conversa comum.

## Busca do projeto ou indexação não retorna resultados

Confirme se o workspace e os arquivos estão incluídos na indexação e se o provider de embeddings exigido pela configuração está disponível. Veja a aba **Memory** da workbench e os logs do backend para identificar falhas de indexação, embedding ou reranking. Essas capacidades podem usar providers distintos do modelo de chat. Consulte [RAG](../../concepts/rag) e [Storage](../../concepts/storage).

## O agente não consegue escrever arquivos ou executar comandos

Confira se o workspace está confiável e se o modo de permissão, a política de ferramentas e o sandbox permitem a operação. O acesso a ferramentas pode exigir aprovação HITL. Não marque como confiável uma pasta cuja origem você não conhece. Veja [Primeiro workspace](../first-workspace) e [Segurança](../../security/).

## Ferramentas de um servidor MCP não aparecem

Confira o estado da conexão em **Configurações → Ambiente → Plugins**. Verifique o comando, argumentos, transporte e logs do conector. Consulte a [referência do cliente MCP](../../reference/mcp-client).

## O modo de storage completo não conecta

O modo `complete` precisa que PostgreSQL, Qdrant e Redis estejam disponíveis nas URLs configuradas (`POSTGRES_DSN`, `QDRANT_URL`, `REDIS_URL`). Para desenvolvimento a partir do código-fonte, consulte o comando `scons docker` no [guia de Docker](../../deployment/docker). O modo local `lite` não exige esses serviços.

## Como obter mais detalhes

Os logs locais ficam em `~/.vectora/logs/`. Remova ou mascare tokens, caminhos privados e conteúdo de conversas antes de compartilhar um trecho.

## Relatar um problema

Procure primeiro nas [issues existentes](https://github.com/vectora-company/vectora/issues). Para uma vulnerabilidade, siga a [política de segurança](https://github.com/vectora-company/vectora/blob/master/SECURITY.md) em vez de publicar detalhes em uma issue.

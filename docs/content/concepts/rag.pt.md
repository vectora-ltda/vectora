---
title: RAG e contexto do projeto
weight: 1
---

RAG (Retrieval-Augmented Generation) permite que o agente recupere trechos de documentos indexados para responder com contexto do workspace. A recuperação depende dos arquivos incluídos, do estado da fila de indexação, do armazenamento selecionado e dos providers de embedding/reranking configurados.

## Fluxo geral

```text
arquivos e documentos selecionados
             │
             ▼
       extração e divisão
             │
             ▼
       embeddings e índice
             │
             ▼
  recuperação de trechos relevantes
             │
             ▼
 contexto enviado ao modelo escolhido
```

O Vectora combina recuperação lexical e vetorial quando esses recursos estão disponíveis na configuração. O reranking é configurável e pode usar um provider compatível. A busca web é uma ferramenta separada e não deve ser confundida com recuperação local. Provider remoto pode receber a consulta e os trechos necessários; confira a política de dados antes de usá-lo com conteúdo confidencial.

## Embeddings e reranking

Os providers e modelos são escolhidos em **Configurações → Ambiente → Modelos de IA**; endpoints e credenciais ficam em **Provider Routing**. As opções incluem providers remotos e configurações locais compatíveis. O Vectora não exige uma chave Cohere para iniciar o chat. A indexação precisa de um provider de embedding funcional, e o reranking pode ser desligado ou configurado separadamente.

## Consultar a base

Use a aba **Memory** da workbench para acompanhar a atividade de recuperação e ajustar as opções de RAG disponíveis. Inclua os arquivos necessários no workspace, aguarde a indexação terminar e verifique as fontes recuperadas antes de confiar que todo o projeto foi indexado.

## Limites

RAG recupera trechos a partir do índice; ele não garante completude, atualidade ou correção. Arquivos ignorados, erros de extração, limites de contexto e configurações do provider afetam os resultados. Confirme afirmações importantes na fonte original.

## Veja também

- [Context Graph](../context-graph) — relações estruturais extraídas do código
- [Usando a workbench](../../guides/using-the-workbench) — painel Memory na prática
- [BYOK e privacidade](../../security/byok-privacy) — fluxo de dados para providers externos

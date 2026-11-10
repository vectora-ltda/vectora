---
title: Início rápido
weight: 3
---

Este guia cobre o primeiro uso depois da instalação. Para compilar o projeto, use o [guia de instalação](../installation).

## 1. Inicie o Vectora e configure um modelo

Abra o aplicativo. Em uma instalação local nova, crie a conta inicial e siga o fluxo de configuração. Em seguida, abra **Configurações → Modelos de IA** para escolher os modelos padrão e configurar os providers necessários. Use **Provider Routing** para endpoints e roteamento.

Configure apenas o que pretende usar. Um provider remoto pode receber as solicitações e os dados necessários à tarefa; modelos locais precisam estar instalados e executando no ambiente do backend. Veja [Configurações](../../guides/using-settings) e [BYOK e privacidade](../../security/byok-privacy).

## 2. Abra um workspace

No seletor de workspace, adicione a pasta do projeto. Um workspace é o limite de contexto para arquivos, terminal, Git, busca e ferramentas do agente.

Um workspace novo começa não confiável. Leia o aviso e confie nele apenas se você conhece a origem e aceita que o agente possa executar as operações permitidas pela configuração. Veja [Primeiro workspace](../first-workspace).

## 3. Faça uma solicitação pequena

Comece com uma pergunta que possa ser conferida:

```text
Resuma a estrutura deste projeto e indique os arquivos que usou como evidência.
```

Depois, teste uma alteração pequena em um branch de trabalho. O agente pode usar ferramentas ou delegar tarefas; permissões, políticas e gates HITL determinam se uma ação precisa de confirmação.

## 4. Explore a busca do projeto

Abra a aba **Memory** da workbench para consultar a base de conhecimento e os resultados recuperados. A indexação depende dos arquivos aceitos e dos providers configurados; verifique o resultado antes de confiar que uma pasta inteira foi indexada. [RAG e Context Graph](../../concepts/rag) explica os limites e as fontes de contexto.

## Próximos passos

- [Usando o chat](../../guides/using-the-chat)
- [Usando as configurações](../../guides/using-settings)
- [Decision Models](../../guides/decision-models)
- [Troubleshooting](../troubleshooting)

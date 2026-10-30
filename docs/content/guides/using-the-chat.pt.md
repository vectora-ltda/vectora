---
title: Usando o Chat
weight: 1
---

## Enviando mensagens

O input do chat aceita texto, arquivos arrastados (drag-and-drop), colar imagens do clipboard, e `@menções` de arquivo pra trazer conteúdo específico pro contexto sem precisar descrevê-lo.

## Seletor de modelo

Mostra só os providers com chave de API configurada — Google Gemini, OpenAI, Anthropic, Cohere, ou Ollama local. Trocar de modelo no meio de uma conversa não perde o histórico.

## Modos de permissão

Controlam o quão automático o agente age antes de pedir sua aprovação:

| Modo                 | Comportamento do gate de aprovação                                                                 |
| -------------------- | -------------------------------------------------------------------------------------------------- |
| **Perguntar sempre** | pausa para ferramentas classificadas como sensíveis ou destrutivas                                 |
| **Aceitar edições**  | permite escrita e edição de arquivos sem pausa; outras ações protegidas continuam sujeitas a HITL |
| **Autônomo**         | não pausa para aprovação humana comum; policy e bloqueios de segurança continuam ativos           |
| **Plano**            | pausa para a primeira ação protegida do turno; chamadas de mídia com custo exigem aprovação própria |
| **Bypass**           | desativa pausas comuns de HITL; guardrails independentes continuam aplicáveis                      |

`Perguntar sempre`, `Aceitar edições`, `Autônomo`, `Plano` e `Bypass` são modos de permissão, não níveis de confiança no modelo. Algumas operações, como controle do desktop, ainda podem exigir confirmação em qualquer modo. Em `Plano`, uma aprovação anterior no mesmo turno permite continuar o plano; isso não transforma a aprovação em uma autorização permanente.

Uma pré-aprovação é uma concessão separada e explícita. Ela vale uma única vez para uma ferramenta e seus argumentos exatos, dentro do mesmo workspace e `run_id`, e expira em prazo limitado. Ferramentas irreversíveis não aceitam essa concessão.

No modo **Autônomo**, um Decision Model só participa quando está habilitado, selecionado e pronto para a superfície da ação. Sem ele, o runtime não deve ser interpretado como se uma decisão automatizada adicional tivesse sido tomada: a policy estática ainda determina o que pode executar. Quando uma avaliação iniciada falha, excede o prazo ou exige revisão humana, a ação não deve ser liberada como se tivesse sido aprovada.

Veja [Orchestrator & Subagentes](../../concepts/sub-agents) para entender o HITL por trás desses gates e [Modelos de decisão](../decision-models) para configurar Decision Models e suas superfícies.

## Thinking / raciocínio do orchestrator

O bloco de "pensando" mostra a decisão do orchestrator antes de agir: responder direto ou delegar pra `coder`/`search`, e por quê. Isso é transparência de verdade, não só um spinner — dá pra ver o raciocínio, não só esperar o resultado.

## Memória entre sessões

Um badge "🧠 N memórias carregadas" aparece quando o agente usa memórias persistentes de conversas anteriores nessa resposta. Gerencie memórias manualmente em **Configurações → Preferências → Memória**.

## Citações RAG

Respostas baseadas em conteúdo indexado trazem citações `[1] [2]` clicáveis — clique pra ver o trecho original e a fonte.

## Multi-usuário (Pro)

No modo Pro com chat web multi-usuário, threads podem ser compartilhadas entre membros do mesmo workspace, com RBAC controlando quem vê o quê.

## Veja também

- [Usando a workbench](../using-the-workbench) — o painel lateral que acompanha o chat
- [Usando as configurações](../using-settings) — onde ficam modelo padrão, idioma, tema

---
title: Modelos de decisão
weight: 2
---

Decision Models avaliam decisões estruturadas do runtime, como o risco de uma chamada de ferramenta. Eles não substituem as permissões da ferramenta, a policy do workspace ou os gates de aprovação.

## Configuração

Na área **Configurações → Modelos de IA**, selecione um provider disponível, configure as credenciais exigidas e habilite o uso de Decision Models. A matriz de superfícies controla onde o modelo pode ser consultado, como HITL, subagentes, Plan Mode, Browser, MCP, terminal, rede e tarefas em segundo plano.

As preferências são associadas ao usuário e ao workspace ativo. Ao trocar de workspace, confira novamente o provider e as superfícies habilitadas. Uma superfície desmarcada não deve ser autorizada por uma preferência de outra superfície.

## Como a decisão afeta uma ação

1. O runtime verifica a configuração e a superfície aplicável.
2. Se a avaliação estiver habilitada e o provider estiver pronto, o modelo retorna uma decisão estruturada.
3. A policy e os controles locais continuam prevalecendo. O modelo pode aumentar a exigência de aprovação ou bloquear uma ação; sua resposta não concede permissões que a policy negou.
4. Se uma avaliação iniciada exceder o prazo, falhar, retornar conteúdo inválido ou exigir revisão humana, a ação não é tratada como aprovada.
5. Se o Decision Model estiver desabilitado ou indisponível antes da avaliação, nenhuma decisão automatizada adicional é presumida; os gates estáticos do runtime continuam valendo.

## Limites

- A habilitação não transforma todos os providers em serviços disponíveis. Credenciais, health, dependências locais e conectividade precisam funcionar no ambiente atual.
- Uma opção marcada na interface indica preferência. Ela não é evidência de que o provider respondeu ou de que a avaliação foi executada.
- Testes unitários e de integração não comprovam disponibilidade live de Clef, Laya ou JEV. Resultados `skipped` devem continuar identificados como não executados.
- Decision Models não substituem confirmação humana quando a policy ou o resultado exige aprovação.

## Segurança e dados

As decisões são associadas ao contexto de execução. Não inclua credenciais ou conteúdo privado em campos de decisão. A interface deve mascarar segredos, e os registros de auditoria devem armazenar apenas metadados e resumos necessários para explicar a decisão.

## Alterações durante uma execução

As preferências de decisão são registradas por execução. Alterar o provider, habilitar ou desabilitar o modelo ou mudar suas superfícies invalida a autorização anterior. Voltar à configuração original não recupera essa autorização: será necessária uma nova avaliação ou aprovação conforme a política. A verificação ocorre novamente antes de chamar a ferramenta, inclusive depois de operações assíncronas de preparação.

## Transcrição local

A transcrição e a sondagem do runtime compartilham um limite de uma operação nativa por processo. Se houver uma operação em andamento, aguarde sua conclusão antes de tentar novamente. A transcrição limita a espera a 60 segundos; a sondagem, a 45 segundos. Exceder a espera não encerra a inferência nativa: o slot permanece ocupado e o áudio temporário é removido quando o processamento termina. Nenhum resultado tardio é apresentado como uma sondagem concluída com sucesso após timeout.

## Gravação e replay de uma execução

Para registrar as entradas reais do modelo, habilite `EVALUATION_RECORDING_ENABLED=true` antes de iniciar o backend. A gravação é opcional e fica desabilitada por padrão; ela armazena contexto e respostas no AgentRun, com redação de credenciais. O conteúdo ainda pode conter informações privadas: controle o acesso e a retenção como no histórico da conversa.

Salve a resposta JSON da consulta autenticada `GET /runs/{run_id}` em `run.json`. Exporte e reproduza a cassete:

```shell
vectora eval export-run --run-file run.json --target run.cassette.json
vectora eval run --suite run.cassette.json --output json
```

A exportação associa as interações às decisões e aos digests dos resultados observados das ferramentas. Operações ainda pendentes são identificadas como resultados incompletos. O replay usa respostas gravadas e não chama providers nem executa as ferramentas novamente. Ele não comprova disponibilidade live ou repetibilidade do ambiente externo.

A aba **Modelos de IA** também reúne a seleção do modelo principal do chat e uma seção própria para compreensão de imagens e fallback de visão. A ordem de fallback do chat permanece separada da geração de imagens, TTS e vídeo.

A gravação de avaliação fica no AgentRun e acompanha a exclusão da sessão. Arquivos JSON exportados são cópias independentes: aplique a eles a política de retenção do seu ambiente e exclua-os separadamente quando não forem mais necessários.

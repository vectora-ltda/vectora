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

---
title: Plan Mode, AgentRun e decisões
weight: 5
---

Este guia explica como planejar uma tarefa antes de executá-la, como acompanhar cada execução do agente pelo AgentRun e como as decisões de aprovação ficam registradas.

> **Status:** estas capacidades fazem parte da linha 0.3. A configuração de Decision Models depende da integração do PR #404 à `release/0.3`. Esta página não declara concluídos os testes live que ainda aparecem como `skipped`.

## Pré-requisitos

- Um workspace aberto no Vectora.
- Um provider de modelo configurado para o chat.
- Para Decision Models: um provider de decisão configurado (veja [Modelos de decisão](../decision-models)).

## Plan Mode

O Plan Mode tem duas formas de uso:

- **Prefixo `/plan`:** uma mensagem que começa com `/plan` é enviada em modo de planejamento. O prefixo é removido do texto antes de chegar ao agente.
- **Modo de permissão `Plano`:** o agente pausa na primeira ação protegida do turno e pede sua aprovação. Depois que você aprova, o plano continua no mesmo turno. A aprovação não vale para turnos seguintes.

O Plan Mode não é a aba **Plan** da workbench. A aba mostra os artifacts e a checklist da sessão; o Plan Mode controla quando o agente pausa. Veja [Usando o chat](../using-the-chat) para todos os modos de permissão.

## AgentRun

Cada execução do agente tem um `run_id`, um estado, evidências e as relações entre o agente principal e os subagentes. O histórico da thread é derivado de um ledger de eventos idempotente.

Os estados possíveis são:

| Estado | Significado |
| --- | --- |
| `planned`, `admitted` | A execução foi criada e aceita, mas ainda não começou |
| `running`, `tool_running` | O agente está gerando ou executando uma ferramenta |
| `waiting_approval` | Aguardando uma decisão humana |
| `waiting_provider` | Aguardando o provider do modelo |
| `verifying` | Rodando os quality gates do workspace |
| `passed`, `degraded`, `failed`, `cancelled` | Estados finais |
| `recovery_required` | A execução foi interrompida e precisa de reconciliação antes de continuar |

As rotas abaixo exigem autenticação:

- `GET /runs/{run_id}`: estado e evidências de uma execução.
- `GET /runs/{run_id}/timeline`: linha do tempo de eventos da execução.
- `GET /threads/{thread_id}/runs`: execuções de uma thread.

No modo local, as execuções ficam em SQLite. Instalações com PostgreSQL usam o mesmo contrato.

## Decisões registradas

### Aprovações humanas (HITL)

Quando o agente pausa, você responde com uma destas decisões:

| Decisão | Efeito |
| --- | --- |
| `approve` | Executa a ação proposta |
| `reject` | Não executa; o agente recebe a recusa como resultado |
| `option:<valor>` | Escolhe uma das opções apresentadas |
| `edit:<json>` | Executa com os argumentos editados |

A decisão fica associada ao `run_id` da execução.

### Acesso a servidores MCP

Antes de usar um servidor MCP, o Vectora avalia a política de MCP e registra o resultado:

| Código | Significado |
| --- | --- |
| `allowed` | O servidor está liberado (ou não há regra) |
| `blocked` | O servidor não está na allowlist aplicável |
| `policy_unavailable` | A política não pôde ser carregada; o acesso é negado |

A regra do workspace tem precedência sobre a regra da instância. A allowlist de MCP controla quais servidores podem ser usados; ela não é uma política de egress (veja [Políticas de egress](../egress-policies)).

### Decision Models

Com a camada configurável de HITL, um Decision Model pode avaliar uma ação e devolver `allow`, `deny` ou `requires_human`. A decisão nunca amplia permissões: policy, gates e controles locais continuam valendo. Veja [Modelos de decisão](../decision-models).

## Limitações

- Uma aprovação do modo `Plano` vale só para o turno em que foi dada.
- Operações como controle do desktop podem exigir confirmação em qualquer modo de permissão.
- Uma execução em `recovery_required` não repete automaticamente ferramentas que já podem ter produzido efeito.
- Decision Models dependem de provider configurado e pronto; um provider indisponível não conta como aprovação.

# HITL e Decision Models

Este documento é a especificação operacional da issue #403. Ele acompanha a
implementação para evitar que a configuração de Decision Models seja apenas um
controle visual.

## Autoridade por modo

| modo           | autoridade                                                           | ausência de Decision Model                              |
| -------------- | -------------------------------------------------------------------- | ------------------------------------------------------- |
| `ask`          | humano, após pausa persistida                                        | continua aguardando humano                              |
| `accept_edits` | policy permite somente edição limitada                               | mantém as pausas restantes                              |
| `plan`         | humano aprova o plano; cada efeito sensível é reavaliado             | não elimina aprovação                                   |
| `auto`         | policy; Decision Model só decide quando habilitado para a superfície | não há proteção de modelo; aplica-se a policy explícita |
| `bypass`       | somente sandbox/policy; não é autorização universal                  | continua sem proteção de modelo                         |

`auto` não significa “sempre permitir”. Quando um Decision Model está
configurado, habilitado para a superfície, autenticado, saudável e dentro do
limiar, ele pode retornar `allow`, `deny` ou `requires_human`. Quando qualquer
predicado falha, o resultado é `decision_model_unavailable` e a policy decide
se pausa ou bloqueia. Nunca há fallback silencioso para “permitir”.

## Superfícies configuráveis

As configurações serão versionadas por workspace/tenant e associadas ao
`run_id`:

`hitl`, `subagents`, `plan_mode`, `kanban`, `browser`, `mcp`, `terminal`,
`network`, `background`.

Cada superfície possui `enabled`, `provider`, `model`, `fallback`, timeout,
limiar de confiança e estado de readiness. O snapshot usado pela execução é
imutável; mudanças posteriores valem apenas para novos runs.

## Fluxo de decisão

1. A tool/capability é classificada pela policy e pelo modo HITL.
2. O runtime resolve o snapshot de Decision Model da superfície.
3. Se o snapshot não estiver pronto, registra indisponibilidade e aplica a
   policy sem fingir que houve decisão do modelo.
4. Se estiver pronto, o adapter SystemOne recebe contexto mínimo e retorna
   decisão tipada.
5. A resposta é validada por confiança, impacto, policy e identidade.
6. O resultado é persistido no AgentRun/ledger e anexado ao Evaluation Lab;
   segredos e prompt privado são redigidos.

## UI prevista

- seletor rápido ao lado do esforço/modelo do chat;
- indicador `ativo`, `desabilitado`, `sem credencial`, `não saudável` ou
  `indisponível`;
- seleção entre Clef local/API, Laya, JEV e futuros adapters;
- aba de configurações de IA separada de Provider Routing;
- matriz de superfície com colunas “sim” e “não”, salvamento automático e
  rollback visual em erro.

## Provas obrigatórias

Cada modo terá testes felizes, negativos e de regressão para: provider sem
token, provider desabilitado, timeout, rate limit, resposta inválida, baixa
confiança, revogação durante execução, restart, concorrência e isolamento de
workspace. Testes live serão marcados separadamente de testes locais.

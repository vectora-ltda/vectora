# Runtime resilience follow-ups

Este plano reúne as correções técnicas que serão implementadas sobre os contratos canônicos existentes. Cada item deverá ser acompanhado por teste unitário, teste negativo e teste de regressão quando houver comportamento persistente ou concorrente.

## Persistência e histórico

- Preservar `is_error`, estado da execução, sequência e cursores nas projeções de histórico.
- Corrigir backfill tardio para manter ordenação determinística quando novos eventos já existirem.
- Reforçar ownership e autenticação nas rotas de execução.
- Redigir credenciais, chaves compostas e identificadores de provider em todos os stores.
- Gerar identificadores substitutos únicos para tool calls sem identificador externo.
- Manter terminalidade, `terminal_at`, idempotência e detalhes redigidos em atualizações.
- Validar migrações e constraints com SQLite e PostgreSQL.

## Modelos de decisão e runtimes locais

- Corrigir readiness e health checks dos adaptadores locais e remotos.
- Garantir reranking completo, envelopes de erro e tratamento de resposta vazia com encaminhamento para aprovação humana.
- Persistir identificadores de conta e manter modelos suportados configuráveis.
- Evitar inicialização duplicada de runtimes e isolar health checks dos testes HTTP.
- Normalizar scores e validar nomes de nós antes da execução.
- Impedir fallback remoto quando desabilitado.
- Expandir cenários negativos, de regressão e de integração para os runtimes locais.

## Política e egress

- Propagar o mesmo snapshot imutável aos subagentes e às retomadas.
- Exigir prova de posse quando o contexto de sessão estiver ausente.
- Finalizar execuções encerradas por repetição ou resposta vazia sem fabricar resultado.
- Cobrir destinos privados, CGNAT, DNS rebinding, redirects e portas.
- Preservar conteúdo não confiável, estado terminal e evidências em gravações concorrentes.
- Aplicar a política de egress em HTTP, fetch, crawl, map, Browser Workbench e MCP.

## DAG, memória e backups

- Rejeitar dependências duplicadas e identificadores de plano divergentes.
- Garantir `row_factory`, heartbeat, lease, limite de tentativas e recuperação após interrupção.
- Impedir execução em DAG terminal e preservar estados terminais e `execution_finished`.
- Proteger `trust_level` contra sobrescritas do modelo.
- Corrigir purge, paginação, projeções PostgreSQL e purga em lotes.
- Isolar arquivos temporários, symlinks e limites de tamanho, com rollback atômico de backups.
- Validar tokens antigos após recuperação de claims, interrupções não autorizadas e cancelamento de subagentes.

## Evidências e validação

- Associar resultados a execução, ledger, snapshot de política e artifacts.
- Manter matriz por capability e provider, diferenciando replay de execução live.
- Registrar retenção, redação, divergências e limitações.
- Executar testes unitários, negativos, regressivos, concorrentes, integração e E2E.
- Executar Ruff, formatação, Bandit, tipagem, verificação de contratos e checks de frontend e services.
- Classificar explicitamente cada teste live como `passed`, `failed` ou `skipped`.

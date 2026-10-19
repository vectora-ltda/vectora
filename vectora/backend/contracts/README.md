# Contratos Canônicos do Backend Vectora (v1)

Este diretório contém os contratos formais versionados do backend Python do Vectora (`vectora/backend/contracts/`).
Eles servem como fonte canônica da verdade para comunicação HTTP, streaming Server-Sent Events (SSE), catálogo de ferramentas (Tools) do agente e catálogo de erros.

## Estrutura do Diretório

- `VERSION`: Versão canônica do contrato (ex: `1`).
- `http/`:
  - `openapi.json`: Especificação OpenAPI completa do backend (sanitizada de segredos e normalizada).
  - `routes.json`: Inventário exaustivo de todas as rotas e métodos registrados na aplicação FastAPI.
  - `critical-routes.json`: Overlay curado das rotas mais críticas (como rotas de saúde e chat), incluindo políticas de autenticação.
- `tools/`:
  - `catalog.json`: Catálogo canônico de todas as ferramentas disponíveis no agente (`ALL_TOOL_SPECS`), incluindo parâmetros, categorização e mutabilidade (se destrutiva).
- `sse/`:
  - `events.schema.json`: Schema JSON de todos os eventos tipados do wire format SSE gerados pelo stream do chat.
- `errors/`:
  - `matrix.json`: Matriz de erros canônicos da API, seus status HTTP correspondentes, categorias e flags de retry.

## Contratos Operacionais

### 1. Separação Estrita de Health Probes

- `GET /health/live`: Liveness probe. Verifica apenas se o processo Python está vivo e respondendo requisições ASGI. Não depende de storage, banco de dados ou provedores externos. Retorna 200 sempre que o processo estiver responsivo.
- `GET /health/ready`: Readiness probe. Avalia se as dependências obrigatórias para persistência de dados estão operacionais (`store` e `lancedb`). Retorna `200` com status `"ready"` se as dependências essenciais responderem, ou `503` com status `"not_ready"` quando houver degradação de persistência.

### 2. Contrato de Wire Format & Identidade de Eventos SSE

- Todo evento emitido na rota de chat deve obedecer ao discriminador canônico `type` definido no catálogo `StreamChatEventPayload`.
- Para deduplicação e rastreabilidade determinística, eventos que envolvem mutação ou delegação carregam `tool_call_id` / `run_id` estável.
- Toda requisição HTTP e stream SSE carrega `X-Correlation-ID`, `X-Request-ID` e `X-Vectora-Contract-Version`.

### 3. Governança e Evolução dos Contratos na Stack

Para qualquer PR subsequente que introduza ou modifique schemas de tools, rotas ou eventos:

1. Os contratos em `vectora/backend/contracts/` devem ser atualizados via `uv run python -m devtools.contracts export`.
2. A suíte de validação de drift `uv run python -m devtools.contracts check` deve passar sem diferenças.
3. Mudanças não podem remover campos obrigatórios de contratos sem versão semântica compatível.

## Gerenciamento e Verificação

Para re-exportar os contratos após alterações na aplicação:

```bash
uv run python -m devtools.contracts export
```

Para verificar em CI se houve drift (divergência não commitada) entre a implementação e os contratos:

```bash
uv run python -m devtools.contracts check
```

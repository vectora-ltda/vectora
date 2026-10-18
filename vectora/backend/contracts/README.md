# Contratos Canônicos do Backend Vectora (v0.3.0)

Este diretório contém os contratos formais versionados do backend Python do Vectora (`vectora/backend/contracts/`).
Eles servem como fonte canônica da verdade para comunicação HTTP, streaming Server-Sent Events (SSE), catálogo de ferramentas (Tools) do agente e catálogo de erros.

## Estrutura do Diretório

- `VERSION`: Versão semântica canônica do contrato (ex: `0.3.0`).
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

## Gerenciamento e Verificação

Para re-exportar os contratos após alterações na aplicação:

```bash
uv run python -m devtools.contracts export
```

Para verificar em CI se houve drift (divergência não commitada) entre a implementação e os contratos:

```bash
uv run python -m devtools.contracts check
```

---
title: Configuração (Referência)
weight: 2
---

## `~/.vectora/config.toml`

Configuração de runtime, gerada/editada por `vectora config` ou pela UI de Configurações. Exemplo mínimo:

```toml
[global]
mode = "desktop"   # ou "cli", "headless", "mcp", "web"

[runtime]
auto_update = true

[server]
host = "0.0.0.0"
port = 8080
```

## Variáveis de ambiente principais

| Variável                                                  | Obrigatória?        | Descrição                                                     |
| --------------------------------------------------------- | ------------------- | ------------------------------------------------------------- |
| `LLM_PROVIDER`                                            | Não (padrão `google-genai`) | Seleciona o provider de chat; configure a credencial correspondente |
| `GOOGLE_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Depende do provider | chave do provedor de LLM escolhido                            |
| `COHERE_API_KEY`                                          | Opcional            | Credencial Cohere para os recursos que estiverem configurados para usá-la |
| `TAVILY_API_KEY`                                          | Opcional            | busca web                                                     |
| `STORAGE_MODE`                                            | Não (padrão `lite`) | `lite` ou `complete` — veja [Storage](../../concepts/storage) |
| `POSTGRES_DSN`                                            | Só em `complete`    | ex: `postgresql+asyncpg://user:pass@host:5432/vectora`        |
| `QDRANT_URL` / `QDRANT_API_KEY`                           | Só em `complete`    | endpoint do Qdrant                                            |
| `REDIS_URL`                                               | Só em `complete`    | endpoint do Redis                                             |
| `TELEMETRY_ENABLED`                                       | Não (padrão `true`) | Ativa a telemetria nativa do agente (início/fim de turno, tool calls, erros não tratados) via logging estruturado |
| `TELEMETRY_OUTPUT_PATH`                                   | Opcional            | Caminho de um arquivo JSONL dedicado para os eventos de telemetria. Se não definido, os eventos vão pro log JSON padrão (`~/.vectora/logs/backend.jsonl`) |

## Hierarquia

```text
defaults.env (embutido)  →  .env (projeto)  →  ~/.vectora/.env (usuário)  →  overrides por usuário (banco)
```

Veja [Configuração](../../getting-started/configuration) pro guia de uso completo.

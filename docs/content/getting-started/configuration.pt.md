---
title: Configuração
weight: 4
---

## Onde os dados ficam

Tudo que o Vectora precisa pra funcionar mora em `~/.vectora/`:

```text
~/.vectora/
├── config.toml             # configuração de runtime (provedores, storage)
├── auth.key                # chave JWT (gerada automaticamente, permissão 600)
├── data/
│   ├── vectora.db          # usuários, sessões, memórias, checkpoints (SQLite WAL)
│   ├── embedding_queue.db  # fila de indexação assíncrona
│   ├── traces.db           # spans de observabilidade
│   └── lancedb/            # banco vetorial (modo lite)
├── artifacts/              # planos/specs gerados pelo agente
├── secrets/
│   ├── system.kdbx         # vault de segredos do sistema (KeePassXC)
│   └── users/{id}.kdbx     # vault por usuário (chaves de API, SSH)
├── skills/{user_id}/       # skills instaladas (SKILL.md)
├── safe_roots.json         # caminhos confiáveis configurados pelo admin
└── workspaces.json         # workspaces registrados
```

## Hierarquia de variáveis de ambiente

Três camadas, a mais específica vence:

```text
defaults.env (embutido no app)  →  .env (projeto, dev)  →  ~/.vectora/.env (usuário, global)
```

Overrides por usuário individual (ex: uma chave de API diferente pra um membro do time) ficam na coluna `env_overrides` do banco, editável via **Configurações → Ambiente → Envs**.

## Modo de storage: lite ou complete

Controlado por `STORAGE_MODE` (`lite` por padrão). Veja o guia dedicado em [Storage: lite vs. complete](../../concepts/storage) pra saber quando cada um faz sentido — resumo rápido: **lite** (SQLite + LanceDB) é zero-infra e aguenta escala real; **complete** (Postgres + Qdrant + Redis) é sobre durabilidade/infra gerenciada, não é um gate de performance. Independente do modo, usuários/auth/config sempre ficam em SQLite.

## Categorias de configuração

As configurações são organizadas por escopo e capacidade. **Preferências** reúne opções pessoais; **Ambiente** contém providers de IA, roteamento, variáveis, skills, plugins MCP e integrações; **Administração** contém controles da instância. A disponibilidade varia conforme o papel do usuário. Consulte [Usando as configurações](../../guides/using-settings) para os detalhes.
## Próximo passo

→ [Primeiro workspace](../first-workspace)

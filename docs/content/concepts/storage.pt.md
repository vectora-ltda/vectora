---
title: "Storage: lite vs. complete"
weight: 4
---

Vectora tem dois modos de storage, controlados pela variável `STORAGE_MODE`. A escolha entre eles é **de infraestrutura, não de capacidade** — os dois modos são totalmente funcionais em produção.

## Os dois modos

|                                 | **lite** (padrão)                  | **complete**                        |
| ------------------------------- | ---------------------------------- | ----------------------------------- |
| Checkpointer (estado do agente) | SQLite                             | SQLite (sempre — veja abaixo)       |
| Vector store                    | LanceDB (embarcado, arquivo local) | Qdrant (busca híbrida dense+sparse) |
| BaseStore (memória longo prazo) | SQLite                             | Qdrant/Postgres                     |
| Cache / KV                      | —                                  | Redis                               |
| Infraestrutura externa          | nenhuma                            | Postgres + Qdrant + Redis           |
| Licença                         | Free ou Pro                        | Requer entitlement Pro              |

## Regra que nunca muda: identidade fica em SQLite

Independente do `STORAGE_MODE`, **usuários, sessões, auditoria e configurações sempre vivem em SQLite** — nunca em Postgres. `storage_mode` afeta só o checkpointer de execução do agente, o vector store e o cache. Isso é uma garantia de produto, não um detalhe de implementação: você nunca perde acesso à sua conta por causa de um Postgres fora do ar.

## Quando lite é suficiente (a resposta curta: quase sempre)

Não existe um limite técnico documentado que force a migração pra complete. LanceDB é local (memory-mapped, sem servidor) e SQLite roda em modo WAL (leitores concorrentes não bloqueiam o escritor). Use lite quando:

- Você usa o Vectora localmente como usuário individual.
- Você quer **zero infraestrutura** — sem Docker, sem serviços externos, sem outra coisa pra manter no ar além do próprio Vectora.
- Você não precisa de replicação/backup gerenciado por um serviço de banco de dados terceirizado.

## Quando complete faz sentido

Não é sobre "aguentar mais usuários" — é sobre **durabilidade e infraestrutura gerenciada**:

- Você já opera Postgres/Redis/Qdrant na sua empresa e quer consolidar backup, monitoramento e alta disponibilidade num único lugar.
- Você quer busca vetorial híbrida (denso + esparso) nativa do Qdrant, em vez do LanceDB local.
- Você quer cache distribuído (Redis) compartilhado entre múltiplas instâncias do Vectora atrás de um load balancer.
- Você tem entitlement Pro ativo e precisa dessa topologia por exigência de infraestrutura ou de uma política de compliance interna. Hospedar os bancos por conta própria não remove o requisito do entitlement.

## Como ativar

```bash
vectora storage wizard
```

O wizard oferece 4 caminhos: Supabase, Neon, Qdrant Cloud, ou Postgres self-hosted — preenche `POSTGRES_DSN`, `QDRANT_URL`, `QDRANT_API_KEY` e já seta `STORAGE_MODE=complete`.

Ou manualmente, via `~/.vectora/.env`:

```env
STORAGE_MODE=complete
POSTGRES_DSN=postgresql+asyncpg://user:pass@host:5432/vectora
QDRANT_URL=https://seu-cluster.qdrant.io
QDRANT_API_KEY=...
REDIS_URL=redis://user:pass@host:6379/0
```

Localmente, `scons docker` (a partir da raiz do repositório, se você roda a partir do código-fonte) sobe os três serviços via Docker Compose pra testar o modo complete sem precisar de um provedor gerenciado.

## Migrando dados entre os modos

```bash
vectora storage migrate status              # lista migrations aplicadas/pendentes
vectora storage migrate upgrade              # aplica migrations pendentes no Postgres
vectora storage migrate to-postgres          # copia schema/dados do SQLite pro Postgres
vectora storage migrate to-qdrant <coleção>  # migra embeddings do LanceDB pro Qdrant
vectora storage migrate to-pgvector          # alternativa: LanceDB → pgvector no Postgres
vectora storage migrate memory-to-native     # memórias antigas → store nativo
```

## Limite prático conhecido

O único número documentado no código é o pool de conexões do Postgres: `min_size=2, max_size=20`. Isso não é um teto de usuários — é o número de queries concorrentes ao banco; acima disso, requisições enfileiram em vez de falhar.

## Veja também

- [Configuração](../../getting-started/configuration) — onde `STORAGE_MODE` é definido
- [Deployment: Docker](../../deployment/docker) — subir a infraestrutura opcional

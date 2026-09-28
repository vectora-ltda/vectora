# Changelog

Este changelog registra as capacidades identificadas na implementação atual do Vectora. O conteúdo é um ponto de partida histórico anterior à versão 0.2.0 e não atribui cada recurso a uma versão específica.

## Capacidades anteriores à 0.2.0

As entradas abaixo descrevem funcionalidades do produto sem reconstruir a origem histórica por commits.

### Features

Aqui precisa de um texto!

#### Vectora

Esta seção reúne a primeira onda de capacidades do aplicativo: armazenamento, dados, memória, indexação e mensageria.

- Oferece armazenamento local em SQLite para sessões, memória, checkpoints, filas de embeddings e dados operacionais.
- Oferece armazenamento completo com PostgreSQL para ambientes que precisam de persistência distribuída.
- Mantém armazenamento vetorial local com LanceDB e armazenamento vetorial remoto com Qdrant.
- Disponibiliza cache distribuído com Redis e fallback local em memória.
- Expõe uma camada KV com backends em memória, Redis e NATS JetStream, incluindo TTL, publicação e assinaturas.
- Oferece mensageria em memória, Redis Streams e NATS JetStream conforme o ambiente configurado.
- Executa um sidecar NATS embutido com descoberta do binário, persistência local, reuso, health check e encerramento seguro.
- Seleciona os backends por modo de armazenamento, mantendo uma configuração local e outra completa para ambientes distribuídos.
- Mantém factories com instâncias reutilizadas por processo e health checks comuns para os backends.
- Preserva usuários, sessões e configurações locais mesmo quando os backends distribuídos estão habilitados.
- Define contratos comuns para stores de memória, sessões, filas, secrets, traces e vector stores.
- Permite buscar memórias por semântica e pesquisar documentos por similaridade vetorial.
- Suporta busca textual lexical e busca híbrida combinando texto e embeddings.
- Permite inserir, atualizar, remover, listar, contar e limpar documentos vetoriais.
- Otimiza tabelas LanceDB periodicamente e mantém índices vetoriais e textuais para as coleções.
- Processa embeddings de forma assíncrona em background sem bloquear o fluxo principal.
- Mantém filas persistentes de embeddings em SQLite ou PostgreSQL.
- Acompanha o estado de cada item e de cada job de ingestão.
- Reprocessa falhas, move itens para uma dead-letter queue e reconcilia a fila após reinicializações.
- Permite arquivar itens pendentes, limpar registros antigos e pausar o worker durante rate limits.
- Usa circuit breaker para proteger provedores de embeddings contra excesso de chamadas.
- Ingere diretórios inteiros com filtragem por extensão, glob e tipo de arquivo.
- Divide documentos em chunks, agrupa chunks por job e expõe o progresso da ingestão.
- Mantém cache de embeddings local ou em Redis e sincroniza a invalidação dos caches.
- Seleciona embeddings entre Cohere, Voyage, Ollama e OpenRouter conforme disponibilidade e configuração.
- Executa curadoria automática do conhecimento indexado e atualiza o manifesto do workspace.
- Organiza conhecimento em buckets de RAG que podem ser criados, listados, ativados, removidos e reindexados.
- Protege buckets em uso com exclusão adiada e mantém o estado de ativação por workspace.
- Unifica a busca entre fatos, Skills e buckets RAG.
- Permite publicar e baixar memory buckets por meio de um catálogo remoto.
- Registra telemetria estruturada em JSON com eventos de turnos e chamadas de ferramentas.
- Mantém tracing persistente em SQLite e registra atividade de threads por usuário e dispositivo.
- Usa PostgreSQL para atividade remota quando disponível e recua para SQLite quando necessário.
- Permite revogar a atividade de um dispositivo e limpar registros antigos.
- Cria checkpoints Git isolados por thread sem alterar a branch ou o índice do usuário.
- Permite restaurar checkpoints e usa snapshots compactados como fallback para workspaces sem Git.
- Gera manifestos de snapshot, aplica limites de tamanho e executa garbage collection dos snapshots antigos.
- Consolida memória de longo prazo em decisões, armadilhas e preferências.
- Mantém histórico das versões de memória e grava alterações de forma atômica.
- Propõe consolidações para aprovação antes de persistir alterações quando essa proteção está habilitada.
- Executa consolidação periódica para usuários ativos sem interromper o fluxo principal em caso de falha.

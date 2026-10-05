# Changelog

As notas abaixo documentam a versão 0.3.0 e preservam o histórico anterior do projeto.

## [0.3.0](https://github.com/vectora-ltda/vectora/compare/v0.2.1...v0.3.0) (2026-10-05)

Esta seção reúne as alterações da linha 0.3 identificadas por milestone e já integradas à base de release.

### Alterações aprovadas e integradas

- Corrige a política de bases empilhadas e permite validação controlada de PRs sobre branches `stack/*` ([#315](https://github.com/vectora-ltda/vectora/pull/315)).
- Promove e sincroniza a linha de manutenção `release/0.2` com a base estável ([#306](https://github.com/vectora-ltda/vectora/pull/306), [#297](https://github.com/vectora-ltda/vectora/pull/297), [#296](https://github.com/vectora-ltda/vectora/pull/296)).
- Estabiliza a seleção de branches versionadas do Release Please e evita releases vazias ([#299](https://github.com/vectora-ltda/vectora/pull/299)).
- Remove objetos de release não rastreados do armazenamento R2 ([#294](https://github.com/vectora-ltda/vectora/pull/294)).
- Corrige a leitura da versão de release a partir da raiz do repositório ([#292](https://github.com/vectora-ltda/vectora/pull/292)).
- Publica a versão 0.2.0 como marco anterior da linha de release ([#283](https://github.com/vectora-ltda/vectora/pull/283)).

As PRs abertas com aprovação registrada na milestone 0.3 permanecem candidatas à integração posterior e não são declaradas como código já publicado nesta versão.

- Instalador desktop completo para a matriz de plataformas ([#295](https://github.com/vectora-ltda/vectora/pull/295), aprovada e ainda aberta).

### Features

A implementação detalhada permanece registrada nas seções históricas abaixo.

Este changelog registra as capacidades identificadas na implementação atual do Vectora. O conteúdo é um ponto de partida histórico anterior à versão 0.2.0 e não atribui cada recurso a uma versão específica.

## Capacidades anteriores à 0.2.0

As entradas abaixo descrevem funcionalidades do produto sem reconstruir a origem histórica por commits.

### Features

Registra as capacidades disponíveis antes da 0.2.0.

#### Vectora App

Esta seção reúne as capacidades do aplicativo, organizadas por área funcional.

##### ReAct e execução

O aplicativo executa conversas com um motor nativo orientado a streaming e ferramentas.

- Executa conversas em um loop ReAct imperativo, recarregando o histórico persistido a cada iteração.
- Controla iterações, volume de ferramentas, subagentes e ações assistidas por pessoa com budgets e guardrails por turno.
- Compacta o contexto quando necessário e detecta chamadas repetidas que possam indicar um loop preso.
- Calcula o orçamento de forma determinística para texto, imagens e argumentos de tools, preserva sempre as instruções de sistema e mantém cada chamada de tool junto dos respectivos resultados ao remover unidades antigas.
- Despacha lotes de ferramentas de forma assíncrona e publica eventos estruturados de texto, ferramentas, resultados, tarefas e estado da execução.
- Oferece uma ferramenta de raciocínio sequencial que registra etapas numeradas, revisões e ramificações antes da ação e sinaliza ao frontend quando a análise foi concluída.
- Reutiliza agentes nativos por usuário, modo e workspace, inicializa e encerra a infraestrutura de persistência no lifecycle do servidor e invalida caches quando o conjunto de tools, a política ou as Skills mudam.

##### Streaming, metas e retomada

O motor mantém o estado da execução observável e permite continuar objetivos e aprovações sem perder o contexto persistido.

- Converte eventos tipados do motor em mensagens SSE para a interface, cobrindo texto incremental, chamadas e atividades de ferramentas, resultados, citações RAG, subagentes, perguntas estruturadas, aprovações, arquivos alterados, métricas, erros e encerramento.
- Executa chamadas de ferramenta do mesmo turno em paralelo com limites controlados, preserva a associação entre chamada e resultado e transforma falhas em mensagens observáveis pelo modelo.
- Avalia objetivos com gates de qualidade e um juiz LLM, gera a continuação do turno quando necessário, detecta falhas repetidas e encerra o orçamento de turnos sem entrar em loop infinito.
- Retoma objetivos interrompidos depois de uma aprovação humana, mantendo os turnos restantes, os gates e a avaliação do objetivo.
- Permite interromper subagentes em execução com token de capacidade autenticado e acompanha liveness por heartbeat antes de encerrá-los por falta de progresso.

##### Provedores LLM e multimodalidade

O motor usa clientes nativos com fallback e adapta capacidades de texto, imagem, áudio e vídeo ao provider escolhido.

- Integra clientes de chat para OpenAI, Anthropic, Google, Cohere, Ollama e OpenRouter por um protocolo comum de mensagens e streaming e usa Voyage para embeddings e reranking.
- Alterna entre providers de chat, embeddings e reranking conforme chaves, disponibilidade, configuração e ordem de fallback.
- Descobre modalidades declaradas por cada modelo e seleciona fallback de imagem quando o modelo ativo não aceita conteúdo visual.
- Suporta geração e análise de imagens, geração de vídeo, transcrição de áudio e texto-para-fala pelos providers que oferecem cada capacidade.
- Expõe busca web nativa e integra resultados ao contexto da conversa com tratamento de falhas e limites de provider.

##### Storage e mensageria

Os backends de persistência e transporte atendem tanto instalações locais quanto ambientes distribuídos.

- Oferece armazenamento local em SQLite para sessões, memória, checkpoints, filas de embeddings e dados operacionais.
- Oferece armazenamento completo com PostgreSQL para ambientes que precisam de persistência distribuída.
- Disponibiliza cache distribuído com Redis e fallback local em memória.
- Expõe uma camada KV com backends em memória, Redis e NATS JetStream, incluindo TTL, publicação e assinaturas.
- Oferece mensageria em memória, Redis Streams e NATS JetStream conforme o ambiente configurado.
- Executa um sidecar NATS embutido com descoberta do binário, persistência local, reuso, health check e encerramento seguro.
- Seleciona os backends por modo de armazenamento e mantém factories reutilizadas por processo com health checks comuns.
- Preserva usuários, sessões e configurações locais mesmo quando os backends distribuídos estão habilitados.
- Define contratos comuns para stores de memória, sessões, filas, secrets, traces e vector stores.
- Aplica um schema idempotente por checksum em SQLite e PostgreSQL, registra histórico, detecta drift e mantém compatibilidade com bancos que usavam migrations numeradas.
- Migra dados de SQLite para PostgreSQL, LanceDB para Qdrant ou pgvector e memória legada para o store nativo, com execução assíncrona, lotes e modo de simulação.
- Sobe e derruba a stack local de PostgreSQL, Redis e Qdrant por Docker Compose ou `docker run`, reutiliza volumes, verifica readiness e expõe URLs e comandos de conexão.
- Monta e testa configurações para Neon, Supabase direto ou via pooler e Qdrant Cloud, incluindo health checks e normalização dos DSNs.

##### RAG e embeddings

A camada de conhecimento combina ingestão assíncrona, indexação vetorial e busca híbrida.

- Mantém armazenamento vetorial local com LanceDB e armazenamento vetorial remoto com Qdrant.
- Permite buscar memórias por semântica e pesquisar documentos por similaridade vetorial.
- Suporta busca textual lexical e busca híbrida combinando texto e embeddings.
- Permite inserir, atualizar, remover, listar, contar e limpar documentos vetoriais.
- Otimiza tabelas LanceDB periodicamente e mantém índices vetoriais e textuais para as coleções.
- Processa embeddings de forma assíncrona em background sem bloquear o fluxo principal.
- Mantém filas persistentes de embeddings em SQLite ou PostgreSQL e acompanha o estado de itens e jobs.
- Agrupa chunks por job de ingestão, expõe contagens por estado para cada operação e mantém o banco da fila em WAL com timeout de escrita para permitir leitura e processamento concorrentes.
- Reprocessa falhas, move itens para uma dead-letter queue e reconcilia a fila após reinicializações.
- Limita tentativas automáticas, arquiva a fila pendente quando o circuit breaker detecta rate limit e deixa itens da DLQ recuperáveis por ação explícita.
- Permite arquivar itens pendentes, limpar registros antigos e pausar o worker durante rate limits.
- Usa circuit breaker para proteger provedores de embeddings contra excesso de chamadas.
- Ingere diretórios inteiros com filtragem por extensão, glob e tipo de arquivo, dividindo documentos em chunks e expondo o progresso.
- Centraliza a contagem de tokens e o chunking recursivo baseado em `tiktoken`, com limites e sobreposição configuráveis compartilhados pela ingestão e pela compactação de mensagens.
- Respeita `.gitignore` e `.vectoraignore`, valida o caminho seguro antes da varredura e permite limitar o resultado sem perder a contagem total de arquivos compatíveis.
- Combina padrões de `.gitignore`, `.npmignore`, `.dockerignore`, `.prettierignore`, `.eslintignore` e atributos do Git com uma lista embutida que exclui ambientes virtuais, builds, caches, chaves, certificados, tokens e arquivos de credenciais antes da indexação.
- Mantém cache de embeddings local ou em Redis e sincroniza a invalidação dos caches.
- Seleciona embeddings entre Cohere, Voyage, Ollama e OpenRouter conforme disponibilidade e configuração.
- Executa curadoria automática do conhecimento indexado e atualiza o manifesto do workspace com uma única síntese LLM por lote, incrementando a versão para que o contexto seja recarregado no próximo turno.
- Organiza conhecimento em buckets de RAG que podem ser criados, listados, ativados, removidos e reindexados.
- Protege buckets em uso com exclusão adiada e mantém o estado de ativação por workspace.
- Unifica a busca entre fatos, Skills e buckets RAG.

##### Memória e contexto

Memória persistente e contexto de workspace mantêm continuidade entre sessões e tarefas.

- Permite publicar e baixar memory buckets por meio de um catálogo remoto.
- Consolida memória de longo prazo em decisões, armadilhas e preferências.
- Mantém histórico das versões de memória e grava alterações de forma atômica.
- Propõe consolidações para aprovação antes de persistir alterações quando essa proteção está habilitada.
- Executa consolidação periódica para usuários ativos sem interromper o fluxo principal em caso de falha.
- Monta o prompt de cada sessão com identidade comum, contexto do workspace, manifesto, documentação de projeto e Skills validadas.
- Lista, cria, edita, remove e limpa memórias persistentes do usuário na interface, com confirmação para exclusão total e estados de carregamento e erro localizados.

##### SubAgents e perfis

Delegação especializada usa agentes isolados, escopos explícitos e rastreabilidade da conversa pai.

- Mantém um catálogo extensível de SOULs para programação, pesquisa, revisão, testes, DevOps, documentação, análise de dados, segurança, QA de navegador e planejamento.
- Dá a cada SOUL um prompt de sistema, uma descrição e um conjunto de ferramentas próprio, aplicado no binding de function-calling.
- Isola agentes que editam arquivos ou Git em worktrees e executa tarefas agendadas com o mesmo catálogo especializado.
- Cria subthreads próprias para subagentes, registra o vínculo com a conversa pai e mantém o histórico de cada delegação separado e rastreável.
- Propaga a política de aprovação do agente principal para subagentes e impede que uma SOUL use ferramentas fora do escopo RBAC do usuário.
- Evita delegações duplicadas com identificadores de correlação, permite interrupção autenticada e encerra subagentes sem progresso por heartbeat.
- Reaproveita a execução em andamento quando a mesma intenção chega novamente, compartilha o orçamento de turnos do agente pai e recusa o spawn antes de criar uma sessão quando o limite é excedido.
- Mantém cada tarefa de conversa ativa associada a um token de capacidade HMAC, permitindo cancelamento imediato autorizado sem confundir uma interrupção explícita com o watchdog de inatividade.
- Permite criar, listar, editar, pausar e remover perfis de agente com escopo de ferramentas, modelo, orçamento e instruções reutilizáveis.
- Valida perfis contra os nomes reais do registry, restringe estados e orçamentos inválidos e persiste título, ícone, cor e instruções por usuário para que a configuração aplicada seja reproduzível entre sessões.

##### Aprovação, segurança e observabilidade

As ações sensíveis passam por políticas de aprovação, proteção de contexto e telemetria persistente.

- Oferece modos de permissão para perguntar, aceitar edições, planejar, executar automaticamente ou exigir revisão antes de ações sensíveis.
- Persiste aprovações pendentes antes de pausar, com opções, prioridade, expiração e argumentos sensíveis redigidos para sobreviver a reinicializações.
- Mantém os argumentos brutos de uma aprovação apenas durante a vida do processo, persiste metadados seguros da operação, provider, modelo, unidades estimadas e chave de idempotência, e limpa a pendência ao resolver ou retomar a execução.
- Mantém allowlists de aprovações por workspace, identifica regras por assinatura estável de ferramenta e argumentos e permite revisar ou remover cada regra.
- Usa avaliação inteligente opcional para decidir pré-aprovações, mantendo o bloqueio seguro quando a avaliação falha ou a política não está disponível.
- Mantém uma allowlist persistente por workspace para operações reconhecidas, usa o comando exato como assinatura de terminal e identificadores opacos para revogar regras sem revelar seus argumentos.
- Usa o avaliador LLM somente como anotação de reconhecimento para operações de leitura; qualquer falha retorna a execução ao HITL normal e nunca transforma a avaliação em autorização.
- Aplica limiares de aprovação para mídia e registra o custo estimado antes de liberar operações que ultrapassam a cota automática.
- Registra telemetria estruturada em JSON com eventos de turnos e chamadas de ferramentas.
- Mantém tracing persistente em SQLite e registra atividade de threads por usuário e dispositivo.
- Oferece janelas de uso curta, de cinco horas e semanal com contagem, limite, restante e previsão de reset para o medidor da sessão.
- Persiste insights semanais somente com metadados técnicos, usa eventos idempotentes, agrega modelos, tokens, ferramentas e custo em janelas de uma, duas ou quatro semanas e omite custos desconhecidos sem expor eventos brutos.
- Formata telemetria em JSONL, limita e resume argumentos de tools, permite arquivo de saída dedicado ou log padrão e transforma falhas de observabilidade em operações sem efeito no agente.
- Registra spans pai/filho com duração, status, tokens e metadados em SQLite, serializa gravações concorrentes e disponibiliza a consulta pelo CLI de diagnóstico.
- Usa PostgreSQL para atividade remota quando disponível, recua para SQLite quando necessário e permite revogar dispositivos e limpar registros antigos.
- Mantém presença de thread apenas com usuário, dispositivo opaco, thread e timestamp, consulta atividade recente de outras instalações no modo complete, retém dados por período limitado e permite revogar uma instalação inteira.
- Trata conteúdo de arquivos, ferramentas e contexto do workspace como dados não confiáveis, preservando a fronteira contra injeção de instruções.
- Detecta padrões comuns de sequestro de instruções, marca resultados externos como conteúdo não confiável e separa o contexto legítimo do workspace das instruções do sistema sem permitir que arquivos liberem gates de aprovação.
- Executa chamadas não destrutivas do mesmo lote em paralelo, força lotes que contenham escrita ou outra ação destrutiva a seguir em ordem e redige tokens conhecidos antes de persistir resultados ou entregá-los ao modelo.
- Mantém um orçamento único por turno para tools, chamadas de rede, subagentes e ações assistidas, travando o primeiro limite excedido e emitindo um erro observável em vez de continuar a execução.
- Expõe perguntas estruturadas com opções, resposta livre opcional, idempotência, cancelamento e expiração; persiste o estado de modo atômico e expira solicitações órfãs após reinicializações.
- Aplica RBAC hierárquico para viewer, member, admin e root, restringindo threads, terminal, auditoria e gestão de usuários por propriedade e papel.
- Avalia regras declarativas de filesystem em ordem, com decisões allow, deny ou interrupt e bloqueio padrão quando nenhum caminho autorizado corresponde.
- Mantém políticas de tools por usuário e um kill-switch global, versiona alterações para invalidar caches do modelo e propaga a mudança entre réplicas pelo KV.
- Persiste raízes seguras com identificadores determinísticos, lock entre processos, transações atômicas e uma raiz padrão protegida para os workspaces do usuário.
- Registra confiança de extensões por origem, assinatura, digest e curadoria, exige confirmação para conteúdo sem confiança e valida novamente o registro antes da instalação.
- Gera debug dumps assíncronos com metadados sanitizados, logs selecionados, configuração não secreta e relatório de QA para investigar instalações sem expor credenciais.
- Suspende operações de storage em janelas de manutenção, aguarda disponibilidade e oferece bypass explícito apenas para rotinas internas autorizadas.

##### Workspaces e Git

O aplicativo oferece isolamento e recuperação para alterações feitas durante uma conversa.

- Cria checkpoints Git isolados por thread sem alterar a branch ou o índice do usuário.
- Permite restaurar checkpoints e usa snapshots compactados como fallback para workspaces sem Git.
- Gera manifestos de snapshot, aplica limites de tamanho e executa garbage collection dos snapshots antigos.
- Navega pela árvore, pesquisa conteúdo, cria, edita, move e remove arquivos e diretórios dentro das raízes autorizadas.
- Oferece stage, unstage, descarte, commit inline, squash, reorder, cherry-pick, revert, merge, checkout, fetch, pull e push.
- Lista branches, worktrees, operações em andamento, pull requests e usuários mencionáveis e permite criar worktrees e pull requests pelo workspace.
- Exibe diffs por arquivo ou referência, histórico de arquivos, detalhes de commit e compara refs com hunks e linhas tipadas.
- Gerencia stash, conflitos de merge, preview e atualização de `.gitignore`, além de transmitir eventos de alteração do workspace.
- Detecta a stack do projeto e expõe configurações de launch, estado, logs e portas dos servidores de desenvolvimento.

##### Interface e workbenches

O aplicativo organiza o trabalho em painéis persistentes que podem ser alternados e redimensionados durante a sessão.

- Oferece workbenches para arquivos, Git/diff, plano, browser, terminal, memória, tarefas, Library e Context Graph.
- Mantém abas, badges de atividade, estado aberto/fechado e dimensões de painel por thread.
- Permite navegar pela árvore de arquivos, fixar arquivos, pesquisar conteúdo e abrir documentos em visualizadores ou editores Monaco.
- Exibe preview de Markdown e PDF, documentos gerados, arquivos alterados e detalhes de commits em janelas do canvas.
- Mostra uma prévia da ingestão com a contagem e os caminhos que os filtros selecionariam, sem indexar arquivos, aplicando debounce e descartando respostas obsoletas durante a edição.
- Detecta imagens, vídeos, áudios e PDFs pelo tipo de arquivo, carrega bytes crus para preview ou download e usa renderização Markdown ou editor de texto para os demais arquivos.
- Abre documentos em diálogos compartilhados entre o canvas e janelas flutuantes, carrega conteúdo sob demanda, mantém o estado de edição e salva alterações no workspace com feedback de erro.
- Oferece histórico de arquivos, criação inline de arquivos e diretórios, diff de alterações e comparação entre referências Git.
- Permite selecionar um commit no histórico de um arquivo, carregar seu diff somente quando necessário e revisar detalhes com lista de arquivos, hunks, descrição redimensionável e editor Monaco somente leitura.
- Compara branches ou refs exibindo avanço e atraso, carrega hunks por arquivo sob demanda, executa merge e apresenta conflitos inline para resolver escolhendo a versão atual ou a comparada.
- Exibe planos e checklists vivos, artefatos da sessão, arquivos tocados e conteúdo de documentos em painéis expansíveis.
- Atualiza o estado de execuções de CI do GitHub a partir de eventos de webhook e mostra toasts de sucesso ou falha mesmo quando o painel Git não está aberto.
- Mantém sessões de terminal PTY multiplataforma com shell interativo, resize, broadcast para várias abas, scrollback limitado com cursor, replay após reconexão e rate limit de entrada.
- Vincula cada terminal ao usuário, thread e workspace corretos, reaplica a política de sandbox quando disponível e encerra a sessão com limpeza do processo e dos consumidores conectados.
- Permite consultar memória da sessão, citações RAG, atividade de web search, jobs de ingestão e busca unificada por tipo de fonte.
- Mostra tarefas em background, execuções, status, resultados, aprovações pendentes e ações de resolução.
- Exibe a Library com busca e seções para conectores MCP, Skills e memory buckets.

##### Contratos de dados e histórico

Os contratos tipados mantêm o mesmo formato entre o loop, a persistência, a API e a interface.

- Valida mensagens completas e fragmentos de streaming com papéis, partes de texto, imagens, raciocínio, chamadas de ferramentas e argumentos parciais.
- Serializa e reidrata mensagens sem perder metadados, nomes, tool calls ou conteúdo multimodal, tolerando registros antigos do histórico.
- Representa entradas de histórico com checkpoint, hunks de diff, arquivos editados e papéis de cada mensagem para a visualização de alterações.
- Expõe snapshots tipados de status, log, diff e operações Git com estados enfileirado, em execução, concluído ou falho.
- Valida metadados de sessão, contexto do agente, métricas de interface, documentos RAG, artifacts, Skills, workspaces e raízes seguras com Pydantic.
- Propaga um contexto tipado por execução com identidade do usuário e da organização, workspace, locale, modelo, modo de permissão, thread, execução em background, store persistente e ID da chamada de ferramenta.
- Mantém tipos de artifacts para planos, especificações, listas de tarefas, guias, arquitetura, implementação, conhecimento aprendido e mídia, permitindo que a interface reconheça o conteúdo mesmo quando sidecars antigos não possuem o tipo.
- Captura o estado inicial do workspace por turno, calcula alterações limitadas ao escopo confiável, reconhece inclusões, remoções, renomes e commits e conserva limites de arquivos, tamanho e runs retidos.

##### Chat e sessões

O chat combina streaming, continuidade de sessões e controles para diferentes modelos e modos de execução.

- Mantém lista de threads, busca de sessões, agrupamento por workspace, criação de novos chats e troca de sessão.
- Reidrata o histórico, recupera sessões interrompidas e mantém rascunhos locais durante mudanças de thread.
- Lista threads com paginação, pins, renomeação, reabertura e exclusão com tombstone, preservando propriedade, cursores de leitura e contagem de atividade por dispositivo.
- Permite anexar arquivos por seleção, arrastar, colar e smart paste, com prévias removíveis antes do envio.
- Renderiza thumbnails da primeira página de PDFs, trechos de código e texto, indicadores de extensão e tamanho e controles próprios para áudio antes do envio.
- Detecta colagens estruturadas, oferece anexar o conteúdo como arquivo ou inserir o texto diretamente e permite capturar screenshots pelo compositor quando o ambiente fornece essa capacidade.
- Classifica colagens de URL, JSON, YAML, HTML, código ou texto sem executar o conteúdo, rejeitando tags YAML customizadas antes de criar um anexo.
- Converte colagens longas em `pasted.txt` para manter o editor responsivo e disponibilizar o conteúdo ao agente como artifact, preservando a alternativa de inserir textos curtos diretamente.
- Suporta entrada de voz, comandos slash, menções de arquivos e workspaces, perguntas estruturadas e prévias de URLs.
- Navega por diretórios do workspace ao digitar `@`, injeta o arquivo escolhido no contexto da mensagem e carrega comandos slash do registry de tools com autocomplete.
- Exibe perguntas estruturadas com opções, resposta livre, cancelamento, expiração e mensagens de erro, além de prévias de URL com título, descrição, origem e imagem quando disponíveis.
- Mantém threads recém-criadas em um registro reativo durante a navegação SPA, evita buscar histórico antes da primeira persistência e remove a marca automaticamente após cinco minutos ou quando o backend confirma a sessão.
- Marca streams ativos em `localStorage`, remove a marca em `done`, HITL, erro ou cancelamento e sinaliza após reload ou crash somente quando a marca ainda não expirou.
- Exibe chamadas de ferramentas, mídia, traces, arquivos editados, citações RAG, uso de tokens e estado do agente.
- Encaminha anexos de imagem, PDF, áudio e texto ou código para a API com tipo, MIME, nome e dados codificados, ignorando arquivos ainda sem conteúdo carregado.
- Permite interromper streaming, retomar aprovações HITL, regenerar respostas, enviar feedback e enfileirar mensagens durante uma execução.
- Classifica falhas de streaming em códigos estáveis para a interface, distinguindo chave ausente, crédito da conta, incompatibilidade de histórico, rate limit, timeout, autenticação e limite de recursão e removendo o JSON cru do provider da mensagem exibida.
- Usa um cliente tipado para SSE e RPC que envia cookies e identificador de dispositivo, renova o access token uma vez após 401 e preserva o destino original ao redirecionar para login.
- Retenta apenas leituras idempotentes após falhas de rede, timeout ou respostas 5xx, com backoff exponencial e jitter; abortos e erros 4xx são propagados sem repetir efeitos colaterais.
- Seleciona modelo, provedor, esforço e modo de permissão, sinalizando incompatibilidades de visão e limites de contexto.
- Mantém ramificações de conversa selecionáveis, compara uma ramificação com outra e permite voltar uma mensagem ao checkpoint do workspace para continuar a partir daquele estado.
- Oferece retry para mensagens que falharam, cópia de respostas e partes de código, renderização Markdown com sintaxe destacada e avaliação positiva ou negativa com comentário.

##### Navegação, sincronização e entrada

A interface reduz o atrito entre sessões, abas e formas de entrada sem perder o estado local do usuário.

- Oferece uma paleta de comandos com busca por categoria, atalhos, navegação por teclado e execução de ações sem sair do chat.
- Registra atalhos globais que respeitam campos de edição e mantém comandos de teclado disponíveis em layouts diferentes.
- Sincroniza workspaces, threads e autenticação entre abas com BroadcastChannel, invalidando caches após criação, renomeação, exclusão ou logout.
- Agenda avisos antes da expiração da sessão, permite renovação explícita com feedback visual e mantém a renovação silenciosa para respostas 401.
- Aceita entrada de voz com estados de escuta e interrupção, além de reprodução de respostas por síntese de fala quando habilitada.
- Permite escolher uma pasta local, navegar por diretórios e discos, criar diretórios, confiar no workspace, iniciar Git e testar conexões SSH ou Codespaces antes de ativar o ambiente.

##### Context Graph e Kanban

Ferramentas visuais transformam o workspace e as tarefas em superfícies navegáveis dentro da sessão.

- Constrói e atualiza um Context Graph incremental com nós, relações, comunidades, god nodes e perguntas sugeridas, executando detecção, análise de AST, semântica, agrupamento e exportação de forma assíncrona.
- Persiste `graph.json`, relatórios Markdown e HTML e um manifesto em `.vectora/context-graph` por workspace, com atualização incremental, filtros de tipos de arquivo, métricas de tokens e retomada por checkpoint quando a análise é interrompida por quota.
- Permite filtrar tipos de arquivo, escolher modos de construção, acompanhar etapas e arquivos processados por callbacks de progresso e consultar o grafo sem bloquear o loop principal.
- Oferece boards Kanban com múltiplos boards, slugs normalizados, arquivamento, colunas, cards, dependências, assignee, progresso e detalhes da tarefa, criando o board padrão sob demanda e impedindo a remoção enquanto houver tarefas.
- Exibe tarefas em execução, revisão e conclusão, permite decompor cards em subtarefas e mantém a linha do tempo de eventos da tarefa para consulta histórica.
- Garante aquisição atômica de tarefas com expiração, bloqueios tipados, transições manuais restritas, aprovação dedicada para revisão e reabertura de tarefas concluídas, além de escalar recorrências após bloqueios transitórios repetidos.
- Publica mudanças de status em SSE com o identificador do board e os dados do bloqueio, mantendo a interface em tempo real sincronizada com o estado persistido.
- Extrai símbolos e relações de código, documentos e manifestos, resolve referências, remove duplicatas e agrupa comunidades semanticamente.
- Resolve símbolos por identificador, rótulo, nome, arquivo de origem ou correspondência única e oferece vizinhança por profundidade, caminhos mínimos e análise BFS de nós afetados por relações de código.
- Calcula nós afetados por uma alteração, diferenças entre snapshots, ciclos de importação, conexões surpreendentes e perguntas para explorar lacunas.
- Exporta snapshots, relatórios e índices do grafo com cache, ingestão incremental e validação de caminhos e conteúdo.

##### Configurações, temas e acessibilidade

As configurações do aplicativo são organizadas por categorias e aplicadas sem deixar o fluxo principal.

- Centraliza preferências de conta, memória, modelos, fallbacks, timezone, temas, escala de UI e atualização automática.
- Permite configurar integrações, plugins, Skills, roteamento de provedores, políticas de ferramentas e allowlists HITL.
- Oferece modos claro, escuro e presets de tema, incluindo conversão e instalação de temas inspirados no VS Code.
- Localiza a interface em português, espanhol e inglês, incluindo mensagens de chat, datas relativas, onboarding, configurações e estados de erro.
- Mantém layout responsivo, painéis redimensionáveis, sidebar compacta, atalhos de teclado, estados offline e avisos de rede.
- Diferencia a perda de conectividade do navegador dos estados da conexão SSE (`idle`, `connected`, `reconnecting` e `failed`) em uma store leve, permitindo que cada painel reaja sem duplicar listeners globais.
- Expõe um catálogo de atalhos por categoria, adapta a tecla modificadora ao sistema operacional, preserva atalhos com modificador em campos de entrada e bloqueia combinações simples enquanto o usuário digita.
- Reúne preferências, ambiente, administração, cobrança e informações do aplicativo em um painel único com categorias carregadas sob demanda.
- Monta categorias de configurações por feature flag, papel e tier, carrega cada painel sob demanda e mantém aliases para links antigos, escolhendo uma categoria válida quando a permissão do usuário muda.
- Persiste preferências por usuário no navegador, hidrata a fonte de verdade do backend no boot e migra escalas de fonte, larguras de painéis e temas instalados sem sobrescrever escolhas personalizadas.
- Permite alternar os modos assistant, IDE e Kanban, posição e largura das sidebars, escala global da interface e tamanho do Monaco com limites próprios para navegador e Electron.
- Lista ferramentas disponíveis, mostra uso e schemas, permite desabilitar ferramentas por usuário e aplica a alteração no próximo request com invalidação do cache de políticas.
- Descobre modelos Ollama no gateway local, pesquisa catálogos OpenRouter e 9Router, registra os modelos escolhidos e remove registros sem duplicar a configuração do provider.
- Configura Telegram, Discord, Slack e e-mail com indicadores de credencial, estado de execução e alternância independente de cada plataforma.
- Permite reordenar fallbacks de modelos, escolher fallback visual para mensagens com imagem e manter a ordem persistida entre sessões.
- Centraliza configurações escalares e coleções em um registry declarativo, com adapters para ambiente, runtime, TOML, modelos registrados e dados por usuário.
- Expõe metadados de categoria, descrição, flag de CLI e segredo para cada configuração e rejeita registros duplicados antes de disponibilizá-los à API ou ao CLI.
- Trata modelos registrados, perfis e memórias como coleções assíncronas com escopo global ou por usuário, preservando operações de listagem, inclusão e remoção sem transformar recursos complexos em simples pares chave-valor.
- Mantém vaults de secrets com providers KeePassXC, SQLite criptografado ou PostgreSQL, incluindo unlock/lock, listagem sem revelar valores e armazenamento de chaves SSH por identificador.
- Persiste preferências não secretas em SQLite com cache em memória, atualização thread-safe, rollback em falha e sincronização apenas das chaves de frontend autorizadas.
- Aplica alterações de chaves de LLM, busca, conectores e tools no arquivo `.env`, no ambiente do processo e nas settings em uma única operação, fazendo a próxima chamada usar a nova configuração sem reiniciar o backend.

##### Autenticação e integrações

O aplicativo conduz a entrada do usuário e a configuração de serviços externos com estados explícitos e credenciais protegidas.

- Redireciona o primeiro acesso para um onboarding guiado e impede que uma instância sem usuários caia em um cadastro incompleto.
- Preserva nome, username, empresa e etapa do onboarding em `sessionStorage` durante recarregamentos causados pela troca de idioma e limpa o rascunho quando o fluxo termina.
- Oferece login local, SSO OIDC quando habilitado, retorno para a rota original após a autenticação e alternância segura para revelar ou ocultar senhas.
- Permite cadastro por convite com papel associado, validação de nome e senha, sugestão de username e verificação debounced de disponibilidade antes do envio.
- Lista integrações a partir do registry do backend, organiza-as por categoria e permite conectar via OAuth ou salvar tokens manuais sem duplicar a definição dos providers no frontend.
- Permite criar variáveis customizadas para providers não catalogados, mantém valores mascarados e oferece remoção, verificação e links de configuração por integração.
- Exibe os estados nunca conectado, conectado e erro do gateway, repete a consulta após falha transitória e atualiza a tela depois de callbacks OAuth.

##### CLI e administração local

O CLI oferece operações de diagnóstico, configuração e manutenção sem depender da interface gráfica.

- Lista sessões persistidas e mostra o histórico operacional em tabelas legíveis no terminal.
- Diagnostica sidecars NATS, disponibilidade de sandbox e condições do ambiente por plataforma.
- Exibe saúde dos backends, testa DSNs, inicializa e encerra stacks locais de Postgres, Redis e Qdrant.
- Oferece wizard para configurar serviços BaaS, executar migrations e consultar o estado do schema.
- Cria e restaura backups manifestados e mantém compatibilidade com formatos legados.
- Lista e invoca ferramentas de mídia com contexto confiável e oferece comandos para chaves, configuração, tarefas e marketplace.
- Oferece wizard de providers que testa a conexão antes de salvar chaves, modelo e provedor ativo, com suporte a Gemini, Cohere, OpenAI, Anthropic e Ollama e fallback opcional de Cohere/Tavily.
- Expõe migrações de schema com status, histórico, plano, checksum e upgrade, além de migrações de dados de SQLite para Postgres, LanceDB para Qdrant ou pgvector e memória para o store nativo.
- Detecta e encerra sidecars NATS órfãos por plataforma e informa a disponibilidade de WSL2, bwrap, Seatbelt ou Singularity para o sandbox antes da execução.

##### Workspaces, Skills e plugins

O ambiente de trabalho mantém isolamento, confiança e extensões com escopo explícito.

- Registra workspaces locais e remotos por caminho, SSH ou Codespaces, com seleção ativa por usuário.
- Identifica cada workspace pelo caminho normalizado, detecta repositórios Git, mantém o workspace ativo por usuário e persiste manifestos com versão incremental.
- Cria `vectora.toml` e `.vectora/` de forma idempotente, interpreta seções de storage, RAG, agente, hooks e controle de desktop, resolve placeholders de ambiente e preserva o arquivo local no Git conforme a regra configurada.
- Separa confiança do diretório da aprovação explícita de hooks, exigindo decisões distintas antes de permitir escrita, terminal, Git ou comandos definidos pelo projeto.
- Mantém Skills instaladas em escopos de usuário, workspace, projeto e runtime, com frontmatter validado e lockfile de dependências.
- Verifica integridade por digest, grava lockfiles de forma atômica e resolve dependências SemVer com retrocesso, rejeitando ciclos, versões incompatíveis, duplicatas e entradas inválidas ou adulteradas.
- Mantém plugins MCP separados por usuário e escopo, com versionamento, cache, health check e política de sandbox.
- Define registros de confiança para conteúdo assinado, curado, com digest ou sem assinatura, exigindo confirmação quando a origem não é confiável.
- Avalia allowlists MCP por instância e workspace, persiste regras locais, aplica versões remotas e falha fechado quando a política não está disponível.
- Importa configurações de MCP e Skills em modo somente prévia, preservando apenas nomes de variáveis de ambiente e removendo credenciais, query strings e fragments das URLs exibidas.
- Resolve arquivos de contexto do workspace por nomes reconhecidos e `.vectora/*.md`, interpreta frontmatter com título, tipo, peso, tags, ativação e momento de injeção, limita o tamanho total e ordena o conteúdo por prioridade.
- Bloqueia um arquivo de contexto quando encontra padrões de prompt injection e mantém diretórios internos, caches e manifestos fora da coleta de contexto livre.

##### Sandbox e segurança de execução

Comandos e ferramentas de sistema podem ser executados em ambientes isolados conforme a política do workspace.

- Lê a seção `[sandbox]` de `vectora.toml` e falha fechado quando a política é inválida ou o backend não é suportado.
- Suporta backends local, Docker, SSH, Modal, macOS, Singularity e Linux nativo conforme o ambiente.
- Detecta WSL2, bubblewrap e sandbox-exec e informa a disponibilidade antes de executar comandos.
- Restringe caminhos sensíveis, como `.env`, chaves privadas, `.ssh` e `.aws`, e controla acessos extras por política.
- Limita execuções em lote por workspace, aplica timeouts e impede que uma execução saturada bloqueie outros workspaces.
- Mantém autenticação por sessão, RBAC, rate limits e escopos de usuário para endpoints e ferramentas.
- Despacha a execução para backends local, Docker, SSH, macOS Seatbelt, Singularity/Apptainer ou Modal conforme a política, falhando fechado quando o backend solicitado não existe.
- Aplica bwrap com seccomp, Landlock para caminhos e portas TCP autorizadas, rlimits no worker e máscaras que impedem acesso a credenciais mesmo dentro do workspace.
- Usa containers efêmeros com rede desativada, filesystem somente leitura e perfis de recursos no modo restrito, ou imagens configuráveis quando o workspace exige dependências específicas.
- Reutiliza transporte SSH para sandboxes remotos, cria processos auxiliares jailados e oferece montagem de comandos em dry-run para auditoria sem executar o processo.
- Mantém isolamento por workspace, quota independente de execuções simultâneas e encerramento por timeout sem bloquear outros ambientes.

##### API, autenticação e provedores

A API expõe contratos REST e streaming para o aplicativo, integrações e clientes externos.

- Oferece signup, signin, refresh, signout, recuperação e troca de senha com cookies httpOnly e tokens rotacionáveis.
- Separa rotas da API, SPA, arquivos estáticos e endpoints públicos, aceita JWT em Bearer ou cookie e tokens de serviço com identidade sintética, valida o identificador do dispositivo e mantém um usuário local virtual quando a autenticação é desativada.
- Aplica limites específicos por IP ou usuário a signin, signup, troca de senha e refresh, usando contadores Redis compartilhados entre réplicas quando disponíveis e memória local como fallback.
- Suporta setup local, convites, perfis, roles administrativas, overrides de ambiente, auditoria e chaves SSH.
- Promove o primeiro cadastro a `root`, mantém o modo local sem conta como setup concluído e conserva usuários, convites, refresh tokens, resets e auditoria no SQLite mesmo quando o restante da instalação usa backends distribuídos.
- Integra login SSO por OIDC e OAuth centralizado para GitHub, GitLab, Google, Slack e outros providers configurados.
- Executa SSO OIDC com descoberta do provedor, PKCE S256, state com expiração, troca assíncrona de código e verificação de assinatura e claims via JWKS.
- Gera tokens de serviço com hash persistido, escopos explícitos, revogação idempotente e exposição do valor bruto somente no momento da criação.
- Aceita tokens de serviço com prefixo identificável, valida escopos sem revelar se uma credencial é inexistente ou revogada e lista apenas metadados para auditoria.
- Deriva usernames normalizados sem acentos e resolve colisões com sufixo verificável, mantendo a identidade local separada do e-mail usado por serviços remotos.
- Resolve o tier Free ou Pro por identidade, aplica o bloqueio com HTTP 402 e informa a URL de upgrade sem fazer chamadas de licença a cada request.
- Valida licenças por token configurado no ambiente ou em `config.toml`, registra o tier e o período de trial, mantém cache online de seis horas e fallback offline de até 48 horas e trata instalações sem token como uso local Free.
- Expõe endpoints para modelos, roteamento de provedores, licenças, feedback, uso, flags, conexão e plugins.
- Mantém clientes nativos e fallback para OpenAI, Anthropic, Google, Cohere, Ollama, OpenRouter e Voyage.
- Consulta uso por provider, cota de mídia e insights semanais de tokens e modelos.
- Descobre tags do Ollama e catálogos de OpenRouter e 9Router, permite registrar modelos selecionados por usuário, mascara credenciais e invalida o cache quando a configuração muda.
- Verifica modalidades de imagem nos catálogos disponíveis e diferencia modelo ausente, catálogo indisponível e suporte visual confirmado antes de escolher um fallback.
- Fornece APIs de threads, mensagens, pins, checkpoints, artifacts, arquivos de turno e eventos SSE.
- Expõe serviços RPC para streaming e retomada de chats, criação e consulta de threads, histórico e catálogo de ferramentas, além de endpoints HTTP para saúde e métricas.
- Mantém um ciclo de vida assíncrono que aplica migrations idempotentes, garante as tabelas de sessões, inicia workers de embeddings, scheduler, consolidação de memória, sincronização de cache e integrações habilitadas, encerrando esses recursos com timeout coordenado.
- Entrega a SPA a partir de bundles Vite, PyInstaller ou Nuitka, permite substituir o diretório estático por ambiente e mantém o modo headless somente com API.
- Acompanha alterações de arquivos por turno com baseline limitado ao workspace, diffs por hunks, status de arquivo e eventos SSE, preservando resultados recentes sem expor caminhos fora do escopo.
- Descobre e registra modelos do Ollama, OpenRouter e 9Router, mantém catálogos com cache e identifica suporte a imagem por modalidade do modelo.
- Permite ativar ou desativar conectores por plataforma, consultar feature flags e sincronizar preferências persistidas do usuário.
- Gera preview de URLs com limites de tamanho, bloqueio de hosts privados, validação de redirecionamentos e timeout de rede.
- Expõe estado seguro do gateway, revoga tokens locais e informa consumo por provider, quota mensal de mídia e insights semanais.
- Recebe feedback com contexto limitado a campos permitidos e rate limit por usuário ou endereço.
- Registra uso de ferramentas e tokens com retenção, deduplicação e agregação por usuário, modelo e período para exibir consumo e insights.
- Identifica credenciais BYOK por provider e modelo, marca a origem de cobrança de mídia no contexto e separa consumo próprio de quotas compartilhadas.
- Protege senhas com Argon2id, usa access tokens curtos e refresh tokens opacos com hash, rotação, expiração e revogação.
- Oferece um contrato assíncrono comum para desbloquear, bloquear, ler, gravar, remover e listar chaves de segredos por usuário, com cofres KeePass KDBX4 derivados da senha de login e fallback SQLite criptografado com SecretBox/NaCl e suporte a ciphertext opaco em Postgres.
- Armazena chaves SSH por usuário em diretórios com permissões restritivas, identifica cada chave por um digest determinístico do conteúdo e lista somente identificadores, carregando os bytes apenas quando necessários ao transporte.
- Mantém overrides de ambiente por identidade local ou autenticada, aplica chaves em tempo de execução e distingue a origem OAuth da configuração manual para impedir a falsificação de chaves-sombra reservadas.
- Protege a chave de assinatura JWT em arquivo com permissões restritas, mantém convites com hash, expiração e idempotência e redige senhas, tokens, segredos, cookies e cabeçalhos de autorização dos metadados de auditoria.
- Implementa recuperação e troca de senha, revoga sessões ao alterar a senha e registra eventos de autenticação no audit log.
- Executa OIDC com descoberta do provider, Authorization Code + PKCE S256, state anti-CSRF, troca de tokens e verificação de assinatura e claims via JWKS.
- Aplica roles root, admin, member e viewer, permissões por recurso, regras de filesystem e políticas de tools por usuário e escopo global.
- Avalia regras de filesystem em ordem de primeira correspondência, com decisões `allow`, `deny` ou `interrupt`, negando por padrão e bloqueando segredos, chaves e pseudo-filesystems do sistema.
- Controla recursos Pro por entitlement e mantém identificadores de dispositivo e tokens de serviço com escopos explícitos e revogação.
- Mantém o controle de mouse e teclado separado do sandbox comum: exige opt-in por workspace, janela previamente descoberta e aprovação humana mesmo quando o modo geral permite execução automática.
- Permite listar, selecionar e focar janelas locais, capturar screenshots e enviar cliques ou texto com validação de coordenadas, limite de tamanho, rate limit, foco confirmado e auditoria sem armazenar conteúdo sensível.

##### Administração e diagnóstico

Administradores controlam o ambiente, os acessos e a saúde dos serviços sem editar arquivos manualmente.

- Lista usuários, altera roles, remove contas com privilégio root e cria, lista ou revoga convites com expiração.
- Consulta e altera políticas de ferramentas por usuário ou globalmente, incluindo kill-switches e resolução do agente com tools, prompt e subagentes efetivos.
- Exibe versão, status de serviços e métricas do sistema e permite atualizar configuração global, ordem de fallback, modelos de imagem e mídia e timezone.
- Administra safe roots com criação, edição, arquivamento e restauração, preservando as raízes embutidas.
- Lista, mascara, atualiza e testa chaves de Google, Cohere e Tavily sem devolver os valores secretos.
- Inspeciona a saúde de checkpointer, store, LanceDB e PostgreSQL, mostra defaults de conexão, testa backends e alterna entre os modos de storage.
- Cria, lista e revoga tokens de serviço com escopo root.

##### Conta, privacidade e distribuição

A conta e o aplicativo possuem controles de assinatura, privacidade e entrega de versões para preservar o acesso e a continuidade do usuário.

- Permite exportar dados da conta em um arquivo privado, com acesso autenticado somente pelo proprietário e armazenamento temporário fora do banco operacional.
- Agenda a exclusão da conta com aviso por e-mail, revoga a sessão atual e executa a remoção definitiva em job separado após o período de retenção, cancelando assinaturas externas quando necessário.
- Oferece checkout e portal de assinatura para planos Pro, com provedores de cobrança locais e internacionais, cupons de desconto ou acesso vitalício e webhooks assinados para atualizar o entitlement após o pagamento.
- Expira presentes com prazo, impede resgate duplicado de cupons e registra eventos de pagamento para que reentregas não concedam a assinatura mais de uma vez.
- Distribui atualizações desktop por canal, sistema operacional e arquitetura exatos, com downloads iniciais sem login, rollout gradual, fallback para versão estável e quarentena automática após falhas repetidas.
- Mantém manifestos e instaladores versionados em armazenamento de objetos e expõe a versão estável do canal para o site e para o atualizador nativo.

##### Backup, compartilhamento e continuidade

Sessões e resultados podem ser preservados, recuperados ou compartilhados com controles explícitos.

- Cria backups manifestados de banco, workspaces, threads e memórias e restaura componentes selecionados em janela de manutenção.
- Lista, pagina, fixa, renomeia, exclui e reabre threads com tombstones e validação de propriedade.
- Cria links públicos de leitura para threads com expiração, revogação, auditoria e sanitização de segredos.
- Gera tokens aleatórios de compartilhamento com prazo limitado, redige credenciais do título e do histórico, registra auditoria obrigatória e desfaz a operação se a trilha não puder ser persistida.
- Armazena artifacts tipados de planos, documentos, guias e mídia por sessão, lista previews limitados, exige autenticação do proprietário e bloqueia traversal em sessões, slugs e nomes de arquivos.
- Persiste feedback permitido em formato estruturado e aplica rate limit ao endpoint de envio.
- Permite inspecionar um backup durante o onboarding e restaurar seletivamente workspaces, threads e memórias antes de entrar no aplicativo.
- Cria links públicos somente para leitura de threads com token aleatório, prazo configurável, sanitização de valores que parecem secrets, auditoria e revogação pelo proprietário.
- Protege a restauração local por token efêmero do bridge desktop, confirmação explícita, lock de operação e janela de manutenção antes de alterar banco ou snapshots.
- Serve artifacts Markdown e mídia gerada com autenticação de proprietário, nomes de caminho sanitizados, previews limitados e bloqueio de traversal.

##### Tools, integrações e automações

O catálogo nativo conecta o agente a serviços externos, tarefas recorrentes e fluxos operacionais.

- Registra ferramentas com schemas derivados de assinaturas e docstrings, metadados de renderização, categorias, ícones e indicação de operação destrutiva.
- Valida argumentos com modelos Pydantic antes da execução, oculta o contexto interno do schema enviado ao modelo e converte falhas de validação ou execução em resultados textuais observáveis.
- Agrupa ferramentas por capacidade e resolve grupos compostos com detecção de ciclos e validação de nomes.
- Integra GitHub para consultar diffs de pull requests e publicar comentários.
- Integra Gmail e Google Drive para listar, ler e pesquisar mensagens, arquivos e pastas usando credenciais OAuth.
- Integra Slack para listar canais, ler mensagens e enviar mensagens com controle explícito de operações destrutivas.
- Integra Linear, Jira e Notion para consultar, criar e atualizar issues, páginas e estados de trabalho.
- Integra Home Assistant para consultar entidades, estados e serviços e executar chamadas de serviço.
- Recebe webhooks assinados de GitHub, GitLab, Linear e provedores de e-mail, persiste eventos e transmite notificações por SSE.
- Conecta eventos externos a tarefas em background e sincroniza eventos de integração com o Kanban.
- Cria rotinas periódicas, tarefas acionadas por webhook ou execução manual, com status, histórico, pausa, retomada e exclusão.
- Agenda tarefas de subagentes com tokens de capacidade, deduplicação por correlação e consulta protegida de resultados.
- Exibe cards Kanban, cria e decompõe tarefas e atualiza status para acompanhar trabalho autônomo.
- Mantém ferramentas de filesystem confinadas ao workspace confiável, com leitura, edição, escrita, busca, listagem, hooks pós-escrita e auto-commit opcional.
- Executa terminal local ou remoto com sandbox, validação de comandos, timeouts, sessões persistentes, streaming de saída e redaction de entradas sensíveis.
- Permite planejar tarefas com listas de todos, pedir respostas estruturadas e registrar pensamento sequencial ou consulta ao agente pai.
- Persiste planos de implementação por workspace em um índice global e, quando disponível, em uma cópia local `.vectora/plans`, permitindo listar o catálogo sem reabrir todos os documentos.
- Controla desktop por opt-in explícito do workspace, com listar, selecionar, focar, capturar tela, clicar e digitar, mantendo auditoria somente de metadados seguros.
- Oferece web search, fetch, crawl e map com Tavily ou fallback sem chave, preservando limites, proteção SSRF e detecção de conteúdo suspeito.
- Obtém transcrições de YouTube por legendas e recorre a áudio transcrito quando elas não existem, além de extrair frames curtos em timestamps.

##### Agendamento e execução em segundo plano

O runtime executa tarefas autônomas com agenda, filas persistentes, orçamento e retomada controlada.

- Cria tarefas únicas, recorrentes, manuais ou disparadas por webhook, com fuso horário do usuário, próxima execução, habilitação, prioridade e associação opcional a boards.
- Converte expressões naturais em português, inglês e espanhol para cron ou atraso único e valida horários antes de persistir o agendamento.
- Mantém histórico de runs, estados de execução, cancelamento, retomada após aprovação, watchdog de heartbeat, quality gates e conclusão sincronizada com o Kanban.
- Separa tarefas `once` de recorrências, desativa a execução única depois de concluída e pula disparos recorrentes muito atrasados para o próximo horário válido, evitando uma rajada após indisponibilidade prolongada.
- Limita tarefas autônomas não manuais por workspace, interpreta o cron no fuso configurado e armazena os próximos disparos em UTC para execução consistente.
- Sincroniza issues do GitHub e alertas de observabilidade com cards idempotentes no Kanban, preservando o `external_id`, convertendo severidade em status e atualizando reentregas sem duplicar tarefas.
- Aplica budgets por tarefa ou perfil, estima custo por tokens, emite avisos de aproximação ao teto e bloqueia novas execuções quando o limite é atingido.
- Usa Redis Streams, NATS JetStream ou fila em memória como fallback, com grupos de consumo, ACK, redelivery, retries e seleção automática do backend disponível.
- Observa alterações do workspace com debounce, publica eventos no KV e mantém um watcher independente por workspace.
- Gerencia o sidecar NATS com JetStream, arquivo de PID/URL, detecção de processos órfãos, readiness, reuso e encerramento multiplataforma.

##### Browser, mídia e desktop

As ferramentas de interação permitem verificar páginas, operar ambientes locais e trabalhar com arquivos multimídia.

- Navega para URLs externas ou servidores de desenvolvimento do workspace em sessões isoladas de browser.
- Permite capturar screenshots, ler o DOM, clicar, preencher formulários, arrastar elementos, enviar arquivos e esperar estados da página.
- Inicia, acompanha logs, reinicia e encerra servidores de desenvolvimento configurados no workspace.
- Detecta servidores ativos, aguarda portas abrirem ou encerrarem e expõe console e requisições de rede do DevTools quando disponíveis.
- Repassa stdout e stderr de sidecars e servidores linha a linha para logs estruturados e para o buffer consultável pela interface, mantendo o tratamento assíncrono até EOF e encerrando árvores de processos sem deixar descendentes órfãos.
- Lista janelas do desktop, seleciona uma janela e executa ações de computador com registro de auditoria.
- Mantém sessões de terminal, permite leitura e escrita assíncronas e redige entradas sensíveis no histórico.
- Inspeciona mídia, extrai frames e áudio e transcreve arquivos locais com ferramentas nativas.
- Mantém sessões e abas Playwright isoladas por workspace, com perfil jailado quando exigido, seleção de aba e políticas para dialogs.
- Mantém perfis persistentes de browser nos escopos global, workspace ou sessão, com locale, retenção, expiração automática, lock entre processos e metadados separados dos cookies e tokens do Chromium.
- Captura console, rede e falhas de requisição por aba através de sessões CDP reutilizadas e buffers limitados.
- Produz snapshots da árvore de acessibilidade com identificadores estáveis para localizar elementos sem depender de seletores frágeis e permite avaliar JavaScript na aba com resultado serializado e erro controlado.
- Emula viewport, escala de dispositivo, CPU e perfis de rede offline, 3G lento ou 3G rápido para validar comportamento responsivo e degradado.
- Inicia e encerra traces de performance, resume eventos por categoria e salva os dados brutos como artifacts do workspace.
- Captura e compara heap snapshots, executa auditorias Lighthouse e grava screencasts PNG como artifacts para inspeção posterior.
- Bloqueia destinos privados, link-local e metadata antes de buscas ou fetches e oferece fallback sem chave com DuckDuckGo e Chromium.
- Inicializa o backend empacotado como sidecar, resolve binários por ambiente, reserva porta livre, aguarda health-check com backoff e encerra toda a árvore de processos ao sair.
- Mantém a ponte IPC tipada entre renderer e processo principal, aceita backend externo em desenvolvimento e trata respostas JSON e falhas de transporte sem travar a interface.
- Gerencia WebContentsView independentes para abas reais do browser, com perfis persistentes, histórico, navegação, eventos de título/favicon/carregamento e bloqueio de esquemas e pop-ups inseguros.
- Pesquisa temas de cor no marketplace do VS Code, filtra extensões de ícones, baixa apenas pacotes permitidos e extrai temas de arquivos VSIX sem instalar conteúdo arbitrário.
- Cria snapshots rotativos do estado do usuário antes de atualizações, valida manifestos e hashes, ignora dados voláteis, limita tamanho dos arquivos e restaura de forma transacional com rollback.

##### Mídia, assets e arquivos gerados

O fluxo multimídia valida os arquivos, controla consumo e entrega os resultados diretamente na sessão que os produziu.

- Resolve `ffmpeg` e `ffprobe` a partir do bundle, do PATH ou dos recursos do projeto e usa os binários com timeouts para inspecionar mídia, extrair frames e separar áudio localmente.
- Transcreve áudio local com modelo embutido sem enviar o arquivo a um provider e valida caminho, assinatura, extensão e tamanho antes de processar a mídia.
- Reserva e finaliza quota de mídia por operação, separa consumo BYOK de consumo gerenciado e registra estados concluído, cancelado, desconhecido ou excedido.
- Gera imagens, áudio e vídeo por providers compatíveis, acompanha jobs assíncronos de vídeo com timeout e persiste URLs relativas que o chat consegue renderizar.
- Valida assets por MIME, assinatura binária, extensão, tamanho máximo e ownership, grava metadados atômicos e remove arquivos da thread sem apagar referências compartilhadas.
- Permite analisar vídeos do workspace, extrair frames sob demanda e servir mídia gerada como artifact com preview e download protegidos.
- Identifica a origem BYOK de uma operação de mídia apenas quando a credencial do usuário para o provider está realmente configurada, removendo marcas antigas do contexto e separando consumo próprio de quota compartilhada.
- Calcula custos por operação com estimativa versionada, reserva unidades de forma idempotente por chamada, libera débitos em falhas ou cancelamentos, permite reativar reservas encerradas e oferece o resumo mensal usado, limite e restante para cada tier.

##### Extensibilidade e Library

A Library amplia o agente por conectores MCP, Skills e buckets de memória instaláveis.

- Conecta servidores MCP por transporte HTTP, SSE ou stdio, descobre suas ferramentas e executa chamadas com timeout e tratamento de erros.
- Instala e remove conectores MCP do catálogo, salva variáveis de ambiente necessárias e verifica o estado da instalação.
- Lista, instala, remove e verifica Skills do catálogo com escopo controlado.
- Mescla catálogos remoto, enterprise e local, valida cada entrada, permite filtrar por texto, categoria e tags sem refazer a consulta a cada busca e instala apenas identificadores resolvidos pelo catálogo nos escopos user, workspace, project ou runtime.
- Lista, instala e publica memory buckets por meio do catálogo remoto.
- Permite aprender fatos de uma sessão, revisar a consolidação e instalar Skills derivadas do conhecimento aprovado.
- Expõe planejamento com listas de tarefas estruturadas e atualizações de progresso no fluxo da conversa.

##### Conectores de conversa e aprendizado

O agente pode ser acessado por canais externos e transformar conhecimento aprovado em memória ou extensões reutilizáveis.

- Conecta Telegram por long polling, Discord por gateway de mensagens, Slack por Socket Mode e e-mail por IMAP/SMTP.
- Normaliza mensagens externas em threads do Vectora, persiste o vínculo por plataforma e identificador externo sem criar threads duplicadas em corridas concorrentes e executa o mesmo loop ReAct com o contexto da conversa.
- Mantém cada plataforma desligada até haver credencial completa e toggle habilitado, bloqueia todas no tier Free, reconcilia estados no boot e após alterações de configuração e isola falhas de um adapter dos demais.
- Distingue credencial salva de habilitação explícita, preserva integrações já ativas no primeiro uso do toggle e devolve o estado `started`, `running`, `stopped` ou `failed` por plataforma para diagnóstico.
- Responde a canais externos com modo de permissão automático, limite de tamanho compatível com as plataformas e conversão de falhas em mensagens observáveis sem derrubar o consumidor.
- Executa mensagens externas pelo mesmo agente nativo e thread persistida do chat web, sem pausar em HITL quando o canal não oferece interface de aprovação e truncando respostas ao limite aceito pela plataforma.
- Usa conexão outbound no Telegram e Discord sem endpoint público, exige os dois tokens do Socket Mode do Slack, ignora mensagens do próprio bot e subtipos que causariam loops e encerra clientes de forma idempotente.
- Lê e-mails não vistos por IMAP, decodifica cabeçalhos e apenas texto simples, limita o corpo enviado ao agente, responde por SMTP com STARTTLS e executa chamadas bloqueantes fora do event loop.
- Destila transcripts em rascunhos tipados de Skills e fatos, remove duplicatas contra o conhecimento existente e exige ação explícita para instalar ou salvar o resultado.
- Gera saídas vazias quando o transcript ou o LLM falha, valida o JSON estruturado, deduplica skills e fatos com normalização estável e só persiste o resultado depois da aprovação explícita.
- Dispara propostas de aprendizado periodicamente por thread, grava a proposta como artifact e adia novos gatilhos enquanto a proposta anterior não for resolvida.
- Avalia o transcript a cada cinco turnos concluídos, ignora instalações sem provider configurado, deduplica contra Skills e fatos existentes e mantém no máximo uma proposta pendente por thread até a decisão explícita.
- Oferece uma camada comum de mensageria que resolve o thread persistente de cada identidade externa, encaminha a mensagem ao agente e devolve uma resposta amigável quando o processamento falha.
- Mantém um gateway WebSocket persistente para webhooks, callbacks OAuth e requisições externas, com registro por fingerprint, segredo separado do token, reconexão com backoff e limite de fila e concorrência para aplicar backpressure.
- Executa revisões self-hosted de pull requests em workers isolados do túnel principal, com deduplicação por entrega, fila limitada, callbacks autenticados com tentativas e encerramento gracioso.

##### MCP, catálogos e eventos externos

As extensões e integrações externas são validadas, instaladas e observadas por contratos próprios antes de alcançarem o agente.

- Valida entradas não confiáveis do registry MCP com schemas, rejeita transportes sem comando ou URL e usa cache local quando a fonte remota está indisponível.
- Consulta o catálogo oficial de servidores MCP e separa essa fonte do registry curado do Vectora, mantendo status de sincronização e expiração de cache.
- Mantém catálogos locais por fonte com validade de seis horas online e fallback de até 48 horas offline, pagina o registry oficial, deduplica servidores e converte apenas pacotes npm/stdio instaláveis em conectores.
- Gera prévias de importação MCP sem executar comandos ou revelar secrets e instala conectores nos escopos user, workspace, project ou runtime autorizados.
- Aplica políticas MCP por instância e workspace, versiona regras, sincroniza alterações remotas e falha fechado quando a política não pode ser carregada.
- Mantém clientes MCP reutilizáveis para stdio, SSE e HTTP, com ambiente filtrado, timeout, lock de conexão, fechamento assíncrono e resultados de ferramentas convertidos para texto.
- Verifica assinaturas HMAC de webhooks GitHub, GitLab, Linear, Resend e Mailgun, persiste eventos de forma idempotente, transmite notificações por SSE e encaminha eventos aceitos para tarefas em background.
- Valida jobs de revisão recebidos pelo gateway com limites de tamanho para diff e metadados, identificadores de entrega e SHAs opcionais antes de enfileirá-los.
- Revisa diffs de terceiros usando o loop nativo com um registry de ferramentas vazio, impedindo que instruções maliciosas no conteúdo acionem filesystem, rede ou subagentes, e exige um modelo local configurado.
- Protege tokens e segredos persistidos do gateway contra leitura ou gravação por outros usuários e nunca inclui corpos ou cabeçalhos sensíveis nos logs de encaminhamento.

##### Transportes remotos e desktop

O mesmo workspace pode operar localmente, por SSH ou em GitHub Codespaces, enquanto o aplicativo desktop mantém seus processos auxiliares sob controle do backend.

- Encapsula listagem, leitura, escrita, criação de diretórios, remoção, execução de comandos e upload/download em uma interface comum de transporte.
- Seleciona e reutiliza o transporte local, SSH ou Codespaces por workspace, mantendo pool de conexão SSH e encerramento coordenado dos transportes.
- Mantém o contrato assíncrono comum para filesystem e comandos, limita leituras, aplica timeouts e reutiliza instâncias por workspace para evitar conexões ou processos duplicados.
- Carrega chaves SSH pelo vault configurado e inicia Codespaces sob demanda antes de executar operações remotas.
- Lista Codespaces via `gh`, inicia ambientes de forma idempotente e executa cada comando remoto por túnel OAuth, devolvendo stdout, stderr e código de saída com tratamento de timeout.
- Inicia o Electron como sidecar opcional em modo desktop, compartilha porta e canal IPC com o backend e reinicia a janela quando o próprio Electron solicita reinício.
- Associa o processo Electron à Job Object no Windows e encerra sua árvore de processos de forma graciosa para evitar órfãos.
- Oferece bandeja do sistema com ações para abrir e sair, mantendo fallback para servidor puro em ambientes sem display, Docker, SSH sem X ou dependências gráficas.
- No Windows, mantém o Uvicorn restrito ao loopback e expõe ao Electron uma named pipe por processo que faz proxy bidirecional transparente, inclusive para respostas SSE longas, encerrando cada conexão quando o lado correspondente cai.
- Expõe um servidor MCP local por stdio para as ferramentas nativas de mídia; a identidade vem do launcher confiável e o transporte não autentica processos remotos arbitrários.

##### Onboarding, atualizações e instalação

A inicialização orienta a configuração local ou remota e preserva o ambiente durante atualizações do aplicativo desktop.

- Conduz o primeiro acesso por identidade, idioma, tema, modo local ou VPS, seleção de workspace, sandbox, chaves de providers e preferências de memória.
- Valida licença por token manual ou OAuth, mostra tier, status e período de trial e acompanha a autorização em outra janela por polling com expiração.
- Divide a configuração inicial em etapas de modo de execução, chaves de providers, criação ou seleção de workspace, memória, capacidades e conclusão, preservando o progresso quando uma etapa falha.
- Valida tokens e conexões de serviços durante a configuração e permite retomar o assistente nas etapas ainda incompletas.
- Mantém comunicação IPC tipada entre renderer e backend, sessão de cookies persistida, deep links `vectora://` e bloqueio de segunda instância.
- Resolve o binário do backend e do NATS por plataforma ou override de desenvolvimento, reserva uma porta livre, aceita backend externo e extrai o pipe IPC mesmo quando o handshake chega fragmentado.
- Aguarda o backend com health-check e backoff exponencial, falha imediatamente quando o processo encerra no boot e inclui os logs recentes no diagnóstico de inicialização.
- Consulta, baixa e instala atualizações com progresso visível, checagem manual ou periódica e respeito à preferência de atualização automática.
- Cria backups rotativos do diretório de dados antes da atualização, verifica manifestos e hashes e restaura automaticamente após falhas de inicialização.
- Mantém até cinco snapshots rotativos com manifestos SHA-256, exclui caches, tokens e segredos, rejeita symlinks e arquivos acima do limite e restaura com validação de caminho, lock e rollback transacional.
- Mantém perfis de navegador Chromium isolados, com armazenamento persistente por perfil, limpeza de dados e validação de esquemas e destinos navegáveis.

### Bug Fixes

### Reverts

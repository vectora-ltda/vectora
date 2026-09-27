---
title: Usando a Workbench
weight: 2
---

A workbench é o painel lateral do chat — uma faixa estreita de ícones sempre visível, mais um painel de conteúdo que abre sob demanda. É onde você trabalha com arquivos, git, terminal e o resto do contexto do projeto sem sair da conversa.

## Abrir e fechar

- Atalho: **Ctrl+\\** (Windows/Linux) ou **⌘+\\** (Mac).
- Clique num ícone da faixa lateral pra abrir aquela aba; clicar na aba já ativa colapsa o painel.
- Botão **X** no cabeçalho do painel fecha a aba atual.

## As 9 abas

### Terminal

Shell real via PTY (`pywinpty`/`ptyprocess`), renderizado com xterm.js. Múltiplas instâncias por sessão (aba "+" pra abrir uma nova), cada uma nomeável. Um terminal abre automaticamente ao montar, se o workspace estiver confiável — workspaces não confiáveis não têm terminal.

### Files

Árvore de arquivos do workspace. Toolbar com novo arquivo/pasta, refresh, e busca inline. Cada item tem ações em hover: `@` (adiciona ao contexto do chat), abrir como janela flutuante, renomear inline, mover pra lixeira (`Del`) ou deletar permanente (`Shift+Del`). Arquivos fixados ("pinned") ficam numa seção separada no topo. Um botão de histórico mostra as versões de um arquivo via `git log`/`git show`.

### Diff (Git)

Duas sub-abas: **Mudanças** (arquivos modificados/staged/untracked, com patch expansível e stage/unstage por arquivo) e **Histórico** (log de commits, clique mostra o diff completo). Modais dedicados pra Stash, Worktrees e criação de PR. Branch selector e botão de sync na toolbar.

### Plan

Lista os artifacts gerados pelo agente na sessão (planos, specs, guias em markdown, via a tool `create_artifact`) e uma seção "Arquivos tocados" com tudo que o agente leu/criou/editou nesta thread.

### Preview

Gerencia servidores de desenvolvimento do seu projeto (lê configurações tipo `launch.json`). Botões play/stop/refresh, um iframe carregando `localhost:<porta>`, e um form pra adicionar um servidor manualmente se não houver config detectada.

### Memory

Timeline ao vivo de buscas RAG e web em andamento, com os trechos recuperados (pílulas expansíveis, separadas em "base de conhecimento" vs. "resultados web"). Um painel de configurações de RAG deixa ajustar reranker, top_k e provider de embedding.

Essa aba também tem o painel **"O que aprendi sobre você"** — uma visão só-leitura dos fatos e skills duráveis que o [Remember](../agent-automation) já salvou por conta própria, sem você precisar lembrar em qual thread o agente aprendeu algo pela primeira vez. Buckets que você importa de outros usuários também vivem aqui, dentro da **Memory Buckets**.

Uma busca no topo da aba consulta fatos, skills e buckets RAG ao mesmo tempo — chips de filtro por tipo restringem o resultado a só um dos três quando você já sabe o que procura.

### Tasks

Rotinas em segundo plano da sessão: tarefas agendadas (cron) ou disparadas por webhook (GitHub/GitLab/Slack). Cada tarefa tem toggle de habilitado, botão de rodar manualmente agora, e um log de execuções anteriores com link pra abrir a thread-resultado.

### Search

Busca em todos os arquivos do workspace. Prefixo `r:` ativa modo regex (ex: `r:function\s+\w+`). Resultados agrupados por arquivo, com preview de linha — clique abre o arquivo na posição exata.

### Context Graph

Grafo de dependências e conhecimento do workspace — veja [Context Graph](../../concepts/context-graph) pra como ele é construído. Aqui você aciona o build, acompanha o progresso por estágio (AST → semântico → concluído), e navega god nodes + perguntas sugeridas.

## Janelas flutuantes

Clicar no ícone de "abrir como janela" num arquivo (aba Files) abre uma **janela flutuante** independente — arrastável, redimensionável (via `react-rnd`), com abas próprias se você abrir mais de um arquivo nela. Minimizar manda a janela pro dock (barra inferior); clicar no dock restaura. Cada janela roda o editor Monaco (texto) ou um visualizador de mídia (imagens/vídeo).

## Veja também

- [Usando o chat](../using-the-chat)
- [Workflows de Git](../git-workflows)

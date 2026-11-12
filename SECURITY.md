# Política de segurança

O Vectora executa ações em nome do usuário (ler e escrever arquivos, rodar comandos, navegar, chamar APIs e servidores MCP) comandado por um modelo de linguagem. Por isso o projeto trata o conteúdo que passa pelo agente como não confiável e aplica limites no lado do backend. Este documento descreve o que existe hoje, o que não é garantido e como relatar um problema.

## Relatando uma vulnerabilidade

**Não abra uma issue pública.** Isso expõe o problema antes da correção.

1. Abra um [Security Advisory privado](https://github.com/vectora-company/vectora/security/advisories/new) neste repositório.
2. Informe o componente afetado, o tipo de falha, os passos para reproduzir e o impacto estimado.
3. Inclua a versão (`vectora --version`), o sistema operacional e o modo de uso (desktop ou servidor).
4. Não envie segredos, tokens nem dados pessoais reais, e não publique um exploit completo antes de combinarmos a divulgação.

Processo: confirmamos o recebimento em até 48 horas, reproduzimos, corrigimos, e publicamos a correção junto com um aviso coordenado, creditando quem relatou, se desejar.

## Versões com correção de segurança

Recebem correção as duas linhas de release ativas, descritas em [CONTRIBUTING.md](CONTRIBUTING.md): a série de manutenção atual e a de desenvolvimento. Versões mais antigas não recebem correção.

## Modos de execução e superfície de ataque

- **Desktop:** o backend e o frontend conversam por IPC local (named pipe no Windows, unix socket nos demais sistemas), sem abrir porta TCP.
- **Servidor (web/VPS):** é a única forma que abre porta TCP. A API exige autenticação em toda rota, exceto uma lista explícita de rotas públicas (autenticação, saúde, licença e documentação), tem limite de requisições por IP e deve ficar atrás de TLS (proxy reverso).
- **Armazenamento:** o modo `lite` usa SQLite e LanceDB locais; o modo `complete` usa PostgreSQL, Qdrant e Redis. Usuários, sessões e configurações ficam sempre em SQLite.

## Proteções existentes

### Autenticação e autorização

- Todo endpoint usa autenticação por padrão (`get_current_user`) e as rotas públicas são uma lista explícita.
- O acesso a dados é escopado por usuário e por workspace; o controle de papéis fica em `backend/rbac/`.

### Terminal, arquivos e regex

Implementadas em `backend/services/security.py`:

- Comandos destrutivos conhecidos são bloqueados antes de executar (por exemplo `rm -rf`, `mkfs` e fork bombs).
- Caminhos são resolvidos (inclusive links simbólicos) e limitados ao workspace; caminhos sensíveis são recusados.
- Padrões de regex vindos do modelo são validados contra backtracking catastrófico, e o `grep` tem timeout.
- Os comandos do terminal rodam com timeout e sem shell persistente entre chamadas.

### Rede (SSRF)

As ferramentas de navegador e de busca recusam destinos internos: IPs privados, de loopback, link-local, reservados, multicast, CGNAT e NAT64 (`backend/browser/ssrf_guard.py`).

### Aprovação humana

Ações de alto impacto passam pelo fluxo de aprovação (HITL) do motor de conversa antes de executar, conforme o modo de permissão da sessão.

### Segredos e telemetria

- Chaves de API são mascaradas antes de qualquer log e ficam em `~/.vectora/config.toml` ou `.env` locais; `.env` nunca vai para o git.
- A telemetria é estruturada e local (`~/.vectora/logs/backend.jsonl`); nenhum evento vai para um serviço de terceiros.
- O Vectora é cliente MCP: ele consome servidores MCP externos e não expõe a si mesmo como servidor MCP.

## Limites conhecidos

Estas proteções reduzem o risco, mas não o eliminam:

- **O modelo pode pedir qualquer ferramenta habilitada.** A lista de comandos bloqueados não cobre todo comando perigoso. Em modo automático, sem aprovação, o agente executa o que pedir; use aprovação humana em máquinas com dados importantes.
- **Injeção de prompt:** o texto de arquivos, páginas e resultados de ferramentas pode conter instruções hostis. O agente trata esse conteúdo como dado, mas nenhum modelo garante imunidade.
- **Servidores MCP e extensões de terceiros** rodam com o poder que você conceder; instale apenas os que você confia.
- **A conexão com o provedor de LLM** envia o conteúdo da conversa a esse provedor, sob a política dele.

## Práticas para quem contribui

- Valide toda entrada, inclusive o que vem do modelo e de `function_results`.
- Mantenha segredos fora do git; confira o `.gitignore` antes de commitar.
- Ferramentas operam com o menor escopo necessário.
- Atenção redobrada a `backend/services/security.py`, a qualquer código que receba caminhos e a toda execução de subprocesso.
- Dependências: `pip-audit`, `bandit` e a varredura de segurança do CI (Snyk) acompanham advisories conhecidos.

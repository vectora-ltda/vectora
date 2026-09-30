---
title: Usando as Configurações
weight: 3
---

Tudo que você configura pela UI vive em três dialogs separados, cada um com um propósito claro.

## Preferências

Configurações pessoais, do seu usuário.

- **Geral** — tema (sistema/claro/escuro/preset/customizado), idioma, system prompt personalizado, ordem de fallback de modelos, e cores customizadas (background, foreground, card, border, primary, accent, muted, sidebar, cor da bolha de usuário).

Os presets **Godot Dark** e **Godot Light** aplicam a paleta Godot ao shell inteiro e sincronizam o tema do Monaco. Eles cobrem GDScript, cenas `.tscn`, recursos `.tres`, `project.godot` e shaders `.gdshader`/`.gdshaderinc`, além das fences `gdscript`, `gd`, `tscn`, `tres`, `godot` e `gdshader` em Markdown e no chat. Outros presets mantêm sua aparência do shell e usam apenas as regras de código Godot.

O suporte desta entrega é lexical: comentários, strings, tipos, keywords, propriedades, referências de nós, folding e brackets. Ele não fornece autocomplete semântico, diagnósticos da engine ou documentação F1. A paleta é uma aproximação do tema padrão da Godot, com estados de editor pré-misturados para o Monaco; marcadores de breakpoint e bookmark só fazem parte do contrato de cores.
- **Memória** — lista de memórias persistentes (pares chave-valor). Adicionar, editar inline, deletar uma ou limpar todas, com timeline de última atualização.
- **Conta** — nome (editável), email (somente leitura), papel/role (root/admin/member/viewer).

## Ambiente

Configurações de integração e extensibilidade.

- **Envs** — variáveis de ambiente por usuário (mascaradas na UI, nunca expostas em texto puro), sobrescrevendo o env do sistema só pra você.
- **Skills** — gerenciador de skills do agente. Instalar via URL git ou path local, ver health-check de cada skill, remover. Cada skill é uma pasta com um `SKILL.md` carregado sob demanda pelo deep-agent.
- **Plugins** — servidores MCP externos configurados pelo usuário. Suporta transporte `stdio` (comando + args), `sse` e `http`. Inclui um painel de **Tool Policy** pra controlar quais tools desse servidor ficam habilitadas.
- **Integrações** — cards de conexão com serviços externos: API key manual (ex: um serviço qualquer) ou OAuth (GitHub, GitLab, Google, Slack). Mostra status conectado/desconectado e a URL de webhook quando aplicável.

## Administração (root/admin)

Configurações da instância inteira — só visível pra administradores.

- **Usuários** — lista com role, criado em, último login. Mudar role, deletar usuário. Seção de **Convites**: criar convite (role + email opcional + TTL 1–720h), copiar URL, revogar convites pendentes.
- **Ferramentas** — lista de tools globais por categoria, com toggle habilitado/desabilitado por tool.
- **Pastas Seguras** — whitelist de caminhos que exigem aprovação extra mesmo depois de confiados.
- **Sistema** — versão do backend, versão do Python, plataforma, status de cada serviço, contagem recente de spans de observabilidade.
- **Configuração** — `allow_public_signup`, modelo padrão da instância, profundidade máxima de recursão do agente, DSN do banco (somente leitura), token de integração.

## Veja também

- [Segurança: Autenticação e RBAC](../../security/authentication)
- [Cliente MCP](../../reference/mcp-client) — como os plugins MCP da aba Ambiente se conectam

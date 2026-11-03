---
title: Políticas de egress
weight: 10
---

Este guia explica como o Vectora controla o tráfego de saída do agente: a política de egress, que vale para as ferramentas de rede, e o isolamento de rede do sandbox, que vale para os comandos executados no worker.

> **Status:** estas capacidades fazem parte da linha 0.3. Esta página não declara concluídos os testes live que ainda aparecem como `skipped`.

## Pré-requisitos

- Um workspace aberto no Vectora.
- Para o isolamento de rede de comandos: o sandbox ativo no workspace (veja [Sandbox](../../concepts/sandbox)).

Configure cada camada conforme as operações que você precisa restringir.

## Configuração

O tráfego de saída é controlado por duas camadas independentes, cada uma configurada em um lugar.

### Duas camadas

| Camada | Onde vale | Como se configura |
| --- | --- | --- |
| Política de egress | URLs iniciais de fetch, crawl e map e navegação do Browser Workbench; veja abaixo as exclusões de redirects Chromium e busca | Configurações de runtime (`egress_policy`) |
| Rede do sandbox | Comandos e arquivos executados no worker jailado | Seção `[sandbox]` do `vectora.toml` |

A allowlist de servidores MCP e as pré-aprovações de ferramentas não são políticas de egress. Elas controlam quais servidores e ações podem ser usados, não para onde o tráfego vai.

### Política de egress

A política é lida das configurações de runtime: `egress_policy:<workspace_id>` contém diretamente a política do workspace; alternativamente, `egress_policy` mapeia IDs de workspace para objetos de política, por exemplo `{"meu-workspace": {"allowed_hosts": ["example.com"]}}`. Para um workspace, um objeto único sem essa chave em `egress_policy` é ignorado e vale a política padrão. Não há tela nem endpoint dedicado para editá-la.

| Campo | Padrão | Efeito |
| --- | --- | --- |
| `block_private_ips` | `true` | Bloqueia destinos que resolvem para IP privado, loopback ou não público |
| `allowed_hosts` | vazio | Se preenchido, só esses hosts são permitidos |
| `denied_hosts` | vazio | Hosts sempre bloqueados |
| `allowed_ports` | `[80, 443]` | Portas permitidas; lista vazia não restringe porta |
| `allow_localhost_dev_ports` | vazio | Portas de loopback permitidas pela camada de egress; `fetch_url` ainda recusa loopback pelo seu guard SSRF independente |

Como a política é avaliada:

1. Só URLs `http` e `https` são aceitas.
2. `denied_hosts` é verificado antes de `allowed_hosts`. Um padrão `*.exemplo.com` cobre `exemplo.com` e todos os subdomínios.
3. O host é resolvido por DNS e cada endereço retornado nessa consulta é verificado. O caminho HTTP direto fixa o IP validado; o fallback Chromium não o fixa, portanto essa verificação não garante proteção contra DNS rebinding nesse caminho.
4. A porta precisa estar em `allowed_ports`, ou em `allow_localhost_dev_ports` quando o destino é loopback.

Subagentes herdam a política da execução que os criou.

### Rede do sandbox

Quando o sandbox está ativo, a seção `[sandbox]` controla a rede dos comandos:

```toml
[sandbox]
enabled = true
backend = "local"
lockdown = false
allow_tcp_ports = [443]
```

- `lockdown = true` nega toda a rede do worker (`--unshare-net`) quando ele roda sob `bwrap` (Linux, ou Windows via WSL2).
- `allow_tcp_ports` nega todo TCP de saída, exceto as portas listadas. A restrição usa Landlock ABI V4 e exige Linux com kernel 6.7 ou superior. O ruleset também trata bind TCP, mas concede apenas connect: um servidor local não consegue fazer bind nem em uma porta listada quando essa restrição está ativa.
- Com `allow_tcp_ports` vazio (o padrão), o sandbox não restringe a rede por porta.

Antes de usar essas capacidades, confira os limites de plataforma, configuração e validação abaixo.

## Limitações

- O backend `local` do sandbox (bubblewrap e Landlock) funciona em Linux; no Windows, depende de WSL2. Sem WSL2, ou sem `bwrap` na distribuição, o worker cai num modo soft que não aplica `--unshare-net`: com `lockdown = true`, os comandos continuam rodando e a rede fica aberta.
- No macOS o sandbox não liga sozinho, e o worker persistente não usa o Seatbelt (`sandbox-exec`).
- Em kernels sem Landlock ABI V4, `allow_tcp_ports` não é aplicado; o worker registra que a restrição não está em vigor.
- O Vectora mapeia quais backends de sandbox conseguem aplicar restrições de rede, mas ainda não recusa a execução quando o backend escolhido não consegue.
- A cobertura da política de egress é a das ferramentas listadas acima; não presuma que outros caminhos de rede usam os mesmos controles.
- `web_search` envia uma consulta, não uma URL: as requisições ao provider de busca e à busca de fallback (DuckDuckGo) não passam pela política de egress.
- `fetch_url` usa Chromium sem `TAVILY_API_KEY` ou quando o provider de extração informa erro de quota/rate limit, mesmo com a chave configurada. Nos dois casos a URL inicial é verificada, mas os redirecionamentos seguidos pelo navegador não são verificados novamente e o IP de destino não é fixado.

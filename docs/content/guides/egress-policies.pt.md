---
title: Políticas de egress
weight: 10
---

Este guia explica como o Vectora controla o tráfego de saída do agente: a política de egress, que vale para as ferramentas de rede, e o isolamento de rede do sandbox, que vale para os comandos executados no worker.

> **Status:** estas capacidades fazem parte da linha 0.3. Esta página não declara concluídos os testes live que ainda aparecem como `skipped`.

## Pré-requisitos

- Um workspace aberto no Vectora.
- Para o isolamento de rede de comandos: o sandbox ativo no workspace (veja [Sandbox](../../concepts/sandbox)).

## Configuração

### Duas camadas

| Camada | Onde vale | Como se configura |
| --- | --- | --- |
| Política de egress | Tools de HTTP e web (busca, fetch, crawl, map) e navegação do Browser Workbench | Configurações de runtime (`egress_policy`) |
| Rede do sandbox | Comandos e arquivos executados no worker jailado | Seção `[sandbox]` do `vectora.toml` |

A allowlist de servidores MCP e as pré-aprovações de ferramentas não são políticas de egress. Elas controlam quais servidores e ações podem ser usados, não para onde o tráfego vai.

### Política de egress

A política é lida das configurações de runtime, pela chave `egress_policy:<workspace_id>` ou pela chave `egress_policy` (um objeto por workspace ou um objeto único). Nesta linha não há tela nem endpoint dedicado para editá-la.

| Campo | Padrão | Efeito |
| --- | --- | --- |
| `block_private_ips` | `true` | Bloqueia destinos que resolvem para IP privado, loopback ou não público |
| `allowed_hosts` | vazio | Se preenchido, só esses hosts são permitidos |
| `denied_hosts` | vazio | Hosts sempre bloqueados |
| `allowed_ports` | `[80, 443]` | Portas permitidas; lista vazia não restringe porta |
| `allow_localhost_dev_ports` | vazio | Portas de loopback liberadas para servidores de desenvolvimento |

Como a política é avaliada:

1. Só URLs `http` e `https` são aceitas.
2. `denied_hosts` é verificado antes de `allowed_hosts`. Um padrão `*.exemplo.com` cobre `exemplo.com` e todos os subdomínios.
3. O host é resolvido por DNS e cada endereço resolvido é verificado. Isso protege contra SSRF e DNS rebinding.
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

- `lockdown = true` nega toda a rede do worker (`--unshare-net`).
- `allow_tcp_ports` nega todo TCP de saída, exceto as portas listadas. A restrição usa Landlock ABI V4 e exige Linux com kernel 6.5 ou superior.
- Com `allow_tcp_ports` vazio (o padrão), o sandbox não restringe a rede por porta.

## Limitações

- O backend `local` do sandbox (bubblewrap e Landlock) funciona em Linux; no Windows, depende de WSL2.
- Em kernels sem Landlock ABI V4, `allow_tcp_ports` não é aplicado; o worker registra que a restrição não está em vigor.
- O Vectora mapeia quais backends de sandbox conseguem aplicar restrições de rede, mas ainda não recusa a execução quando o backend escolhido não consegue.
- A cobertura da política de egress é a das ferramentas listadas acima; não presuma que outros caminhos de rede usam os mesmos controles.

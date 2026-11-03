---
title: Sandbox
weight: 5
---

Sandbox é a camada de isolamento do Vectora para o acesso do agente a terminal e filesystem. Segue o design do [ai-jail](https://github.com/akitaonrails/ai-jail), do Fabio Akita — as ações do agente rodam *dentro* da jaula, não atrás de uma única chamada de função sandboxed — adaptado à arquitetura do Vectora: um único processo de backend de longa duração servindo vários workspaces simultâneos, em vez de um processo por invocação.

## Modelo de ameaça

Sem sandbox, `terminal`, `file_write` e `file_edit` operam diretamente no processo do backend — o agente (ou uma injeção de prompt) pode ler `~/.ssh`, exfiltrar segredos ou rodar comandos destrutivos com os mesmos privilégios do próprio backend. O Sandbox existe pra limitar esse raio de dano aos `rw_paths`/`ro_paths` declarados de um workspace, sem abrir mão completamente do acesso a arquivo e terminal (isso anularia o propósito de um agente de código autônomo).

## Ativação por workspace

O Sandbox é configurado por workspace. Se o workspace **não tem** `vectora.toml`, o Vectora liga o sandbox sozinho quando o ambiente suporta isolamento nativo (`bwrap` no Linux, WSL2 no Windows); sem esse suporte, e sempre no macOS, ele fica desligado. Se o workspace tem `vectora.toml`, vale a seção `[sandbox]`:

```toml
[sandbox]
enabled = true
backend = "local"          # local (bwrap, Linux) | docker | ssh | modal
rw_paths = ["."]           # graváveis dentro da jaula
ro_paths = []               # só leitura dentro da jaula
mask = [".env", "**/*.pem", "**/.ssh/**", "**/.aws/**"]
no_gpu = true
lockdown = false            # true: fail-closed em qualquer ambiguidade de política
```

Se o `vectora.toml` existe sem a seção `[sandbox]`, o sandbox fica desligado e o workspace se comporta exatamente como antes dessa feature existir. TOML malformado ou um campo inválido, uma vez que `[sandbox]` está presente, falha **fechado** (lockdown) em vez de rodar desprotegido silenciosamente.

Para o controle de rede do worker (`lockdown` e `allow_tcp_ports`) e para a política de egress das ferramentas de rede, veja [Políticas de egress](../../guides/egress-policies).

## Como funciona: um worker por workspace

Em vez de criar um processo sandboxed novo a cada chamada de tool, o Vectora mantém um único worker jailado persistente vivo por workspace com `[sandbox]` habilitado — nasce na primeira ação sandboxável, é encerrado quando o workspace fica ocioso. `file_write`, `file_edit` e comandos `terminal` pontuais roteiam por esse worker via um protocolo JSON-lines pequeno (`exec`/`read_file`/`write_file`).

No backend `local` (Linux, ou Windows via WSL2), o worker roda sob `bwrap` quando disponível (Bubblewrap) com:

- Namespacing de filesystem restrito a `rw_paths`/`ro_paths`.
- Paths mascarados (`mask`) nunca expostos dentro da jaula, mesmo se caírem sob um path permitido.
- Um filtro seccomp-BPF real (via `libseccomp`) negando uma lista fixa de syscalls perigosas (`ptrace`, `mount`, `unshare`, `bpf`, carregamento de módulo de kernel, etc.) — se `libseccomp` não estiver instalado no host, o worker ainda roda sob isolamento de namespace, só sem o filtro de syscall, e essa degradação é registrada em log.

No Windows, a ativação automática verifica uma distribuição WSL2 elegível, não a presença de `bwrap` dentro dela. Se `bwrap` estiver ausente, o worker usa um processo soft nativo sem isolamento de filesystem ou rede, mesmo com `lockdown = true`. No macOS não há ativação automática: o worker local persistente tenta usar `bwrap`, não Seatbelt (`sandbox-exec`).

O modo soft não concede dispensa de HITL por sandbox. Até confirmar um worker vivo com isolamento efetivo, valem as regras normais de aprovação; depois que o HITL depende desse isolamento, uma substituição por worker soft é recusada.

Os demais backends (`docker`, `ssh`, `modal`) rodam o mesmo modelo de worker contra seu respectivo primitivo de isolamento; `docker` também nega acesso a rede sob `lockdown`.

## Limitação conhecida: terminal interativo

A chamada pontual da tool `terminal` e as tools de arquivo já são sandboxed hoje. Uma sessão de terminal **interativa** (a aba Terminal, baseada em PTY) ainda não é roteada pelo worker jailado — iniciar um terminal interativo num workspace sandboxed retorna um erro claro em vez de cair silenciosamente pra um shell sem sandbox. Está previsto pra uma iteração futura.

## Veja também

- [Usando o Workbench](../../guides/using-the-workbench) — as abas Terminal e Arquivos na prática
- [Sessões e Workspaces](../sessions-and-workspaces) — como um workspace é confiado e delimitado

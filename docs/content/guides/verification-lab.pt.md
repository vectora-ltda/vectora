---
title: Verificação e Evaluation Lab
weight: 9
---

Este guia mostra como configurar os quality gates que verificam o resultado de uma execução e como usar o Evaluation Lab para gravar, reproduzir e comparar execuções.

> **Status:** estas capacidades estão incluídas na versão 0.1.24. Testes live marcados como `skipped` não comprovam funcionamento em ambiente real.

## Pré-requisitos

- Um workspace aberto no Vectora.
- Os comandos usados pelos gates (por exemplo `pytest`, `npm test` ou um linter) instalados no ambiente do workspace.
- Para o Evaluation Lab: a CLI `vectora` disponível no terminal.

Configure cada camada conforme as operações que você precisa restringir.

## Configuração

A verificação é configurada por workspace num arquivo de perfil; o Evaluation Lab é usado pela CLI.

### Quality gates

Depois de uma execução, o Vectora roda os gates do perfil de verificação do workspace. O perfil fica em `verification/<workspace_id>.json`, dentro da home do Vectora (`~/.vectora` por padrão, ou o diretório definido em `VECTORA_HOME`).

Sem perfil salvo, o workspace usa um perfil padrão ativo e sem gates: nenhuma verificação bloqueia a execução.

#### Campos do perfil

| Campo                           | Descrição                                                        |
| ------------------------------- | ---------------------------------------------------------------- |
| `enabled`                       | Liga ou desliga a verificação automática                         |
| `gates`                         | Lista de gates (veja abaixo)                                     |
| `require_clean_git`             | Exige árvore de trabalho limpa depois da execução                |
| `inject_stack_trace_on_failure` | Reinjeta a saída de erro no próximo turno para o agente corrigir |

Cada gate tem:

| Campo             | Descrição                                                                                      |
| ----------------- | ---------------------------------------------------------------------------------------------- |
| `id`, `name`      | Identificador e nome exibido                                                                   |
| `command`         | Comando e argumentos, como lista (não pode ser vazio)                                          |
| `policy`          | `block` (falha o run), `degrade` (marca como degradado) ou `inform` (só avisa); padrão `block` |
| `file_patterns`   | Padrões glob dos arquivos que ativam o gate                                                    |
| `timeout_seconds` | Tempo máximo, de 5 a 3600 segundos; padrão 300                                                 |

Exemplo:

```json
{
  "workspace_id": "meu-workspace",
  "enabled": true,
  "gates": [
    {
      "id": "pytest",
      "name": "Testes Python",
      "command": ["pytest", "-q"],
      "policy": "block",
      "file_patterns": ["**/*.py"],
      "timeout_seconds": 600
    }
  ],
  "require_clean_git": false,
  "inject_stack_trace_on_failure": true
}
```

#### Resultados

Cada verificação termina em um destes resultados: `passed`, `failed`, `infra_error`, `not_run`, `blocked`, `degraded` ou `unverified`. Um teste que não rodou (`not_run`) é diferente de um teste aprovado. Cada gate também informa o motivo: `passed`, `assertion_failed`, `timeout` ou `dispatch_error`.

Se o arquivo do perfil estiver corrompido, o Vectora aplica um gate de proteção que falha com política `block`, em vez de ignorar a verificação.

### Evaluation Lab

O Evaluation Lab reproduz cassetes existentes sem chamar o provider externo. A CLI não captura interações live diretamente. Habilite `EVALUATION_RECORDING_ENABLED=true` antes de executar para registrar as interações do modelo no AgentRun. Salve o JSON da resposta autenticada de `GET /runs/{run_id}` e use `vectora eval export-run --run-file run.json --target cassette.json`; o comando usa `cassette_from_run` e recusa execuções sem interações gravadas. Excluir a sessão remove o trace armazenado, mas os arquivos exportados exigem retenção e exclusão separadas. Apenas campos e padrões conhecidos de credenciais são ocultados; mensagens e respostas podem manter código privado ou dados pessoais. Restrinja o acesso e revise as cassetes antes de compartilhar.

```bash
vectora eval export-run --run-file run.json --target cassette.json
vectora eval run --suite caminho/para/suite.json
vectora eval compare baseline.json candidata.json
```

- `vectora eval run` reproduz uma única cassete (`--suite` aponta para um arquivo de cassete; uma lista de cassetes é recusada).
- `vectora eval compare` compara duas cassetes (baseline e candidata).
- Os dois comandos aceitam `--output human` (padrão) ou `--output json`.

Confira os limites abaixo antes de depender do replay ou compartilhar uma gravação.

## Limitações

- Replay reproduz uma cassete gravada; ele não prova que o provider, o Browser Workbench ou outra integração estejam disponíveis ao vivo.
- Validações live marcadas como `skipped` não contam como aprovação.
- Os gates rodam no ambiente do workspace; um comando ausente (`dispatch_error`) ou um timeout produzem o resultado `infra_error` com status terminal `failed`; isso não aparece como falha de teste `assertion_failed`.

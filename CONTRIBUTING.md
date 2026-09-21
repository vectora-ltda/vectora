# Contribuindo com o Vectora

Este arquivo reúne as regras de contribuição relacionadas ao fluxo de branches, milestones e releases. A documentação pública do site permanece em `docs/`; detalhes operacionais de desenvolvimento devem ficar aqui ou nos workflows do repositório.

## Linhas de release

O branch `master` é a linha de desenvolvimento da próxima minor. A linha `release/0.1` recebe correções de manutenção da série `0.1.x`.

PRs destinados à manutenção devem usar a milestone `0.1.x`, tanto quando apontam para `master` quanto quando apontam para `release/0.1`. O trabalho do ecossistema VEXT é a exceção: PRs VEXT apontados para `master` usam a milestone `0.2`.

Uma milestone deve ser escolhida antes da aprovação do PR. O workflow `Validate release milestone` verifica a combinação entre base, milestone e tipo de mudança e bloqueia combinações incompatíveis.

## Release Please

O Release Please mantém manifests e changelogs separados por linha. A branch `release/0.1` publica patches `0.1.x`; `master` publica a manutenção `0.1.x` e prepara a próxima minor VEXT em `0.2` conforme a milestone do PR.

PRs automatizados do Release Please são aceitos apenas quando o head pertence ao repositório Vectora, usa o nome de branch gerado pelo workflow e possui o label `autorelease: pending`. Título ou label sem a origem e a branch esperadas não são suficientes para isentar a validação.

## Propagação de correções

Uma correção aplicada em `release/0.1` deve ser encaminhada explicitamente para `master` por merge ou cherry-pick. Mudanças de `master` não são backportadas automaticamente para a linha estável; o PR de propagação deve receber a milestone compatível com a linha que será publicada.

## Commits e revisão

Cada mudança lógica deve usar uma mensagem de Conventional Commits, como `fix: ...`, `feat: ...`, `docs: ...` ou `chore: ...`. Antes de pedir revisão, confirme que a milestone está correta e que os checks obrigatórios do PR passaram.

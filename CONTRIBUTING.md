# Contribuindo com o Vectora

Este arquivo reúne as regras de contribuição relacionadas ao fluxo de branches, milestones e releases. A documentação pública do site permanece em `docs/`; detalhes operacionais de desenvolvimento devem ficar aqui ou nos workflows do repositório.

## Linhas de release

O branch `master` é a linha da próxima minor (`0.2`). A linha `release/0.1` é o destino de manutenção da série `0.1.x`; ambas são publicadas pelo Release Please em PRs independentes.

PRs que apontam para `master` devem usar a milestone `0.2`, incluindo o trabalho do ecossistema VEXT. PRs de manutenção que apontam para `release/0.1` devem usar `0.1.x`. A milestone acompanha a linha de destino porque o Release Please calcula cada branch a partir dos commits que foram incorporados nele.

Uma milestone deve ser escolhida antes da aprovação do PR. O workflow `Validate release milestone` verifica a combinação entre base, milestone e tipo de mudança e bloqueia combinações incompatíveis.

## Release Please

O workflow do Release Please é disparado por pushes em `master` e `release/0.1`. Ele calcula cada linha de forma independente e usa o alvo explícito para criar ou atualizar a PR correspondente: `master` gera a próxima minor e `release/0.1` gera a próxima versão patch `0.1.x`. Milestones não filtram commits depois do merge; o branch de destino é o que separa os históricos.

PRs automatizados do Release Please são aceitos apenas quando o head pertence ao repositório Vectora, usa o nome de branch gerado pelo workflow e possui o label `autorelease: pending`. Título ou label sem a origem e a branch esperadas não são suficientes para isentar a validação.

## Propagação de correções

Uma correção aplicada em `release/0.1` deve ser encaminhada explicitamente para `master` por merge ou cherry-pick quando também fizer parte da próxima minor. Mudanças de `master` não são backportadas automaticamente para a linha estável; o PR de propagação deve receber a milestone compatível com a linha que será publicada.

## Commits e revisão

Cada mudança lógica deve usar uma mensagem de Conventional Commits, como `fix: ...`, `feat: ...`, `docs: ...` ou `chore: ...`. Antes de pedir revisão, confirme que a milestone está correta e que os checks obrigatórios do PR passaram.

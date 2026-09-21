# Linhas de release e milestones

Este documento define como o repositório mantém a próxima minor e a linha de manutenção em paralelo.

## Linhas suportadas

O branch `master` recebe o desenvolvimento da próxima minor e usa a milestone `0.2`. A linha `release/0.1` nasce de `v0.1.22`, recebe somente correções da série `0.1.x` e usa uma milestone patch exata, começando por `0.1.23`.

Cada PR deve apontar para uma dessas bases e ter exatamente uma milestone compatível. A milestone expressa o objetivo de release; ela não substitui revisão, aprovação ou os checks de CI.

## Release Please

O workflow `.github/workflows/release-please.yml` é acionado em ambas as bases e passa `target-branch` explicitamente. A concorrência é separada por branch, e o título gerado identifica a linha (`chore(master): release ...` ou `chore(release/0.1): release ...`). Git isola o manifest, a configuração e o changelog em cada branch; não são necessários arquivos duplicados com nomes diferentes.

O manifest de `master` continua no histórico da próxima minor e o manifest de `release/0.1` começa em `0.1.22`. Assim, commits `fix:` na linha estável podem gerar `0.1.23` sem misturar os commits de `master`, enquanto commits de `master` podem preparar `0.2.0` independentemente.

Tags `v0.1.x` e `v0.2.x` passam pelo mesmo pipeline de entrega porque ambas são linhas suportadas. Se uma linha deixar de ser estável, a política de publicação deve ser revisada antes de desativá-la.

## Propagação e feature freeze

Uma correção que existe em `release/0.1` deve ser encaminhada explicitamente para `master` por forward-merge ou cherry-pick. Mudanças de `master` nunca são backportadas automaticamente para a linha estável. O PR de propagação recebe a milestone `0.2` e passa novamente pelos checks.

Durante o feature freeze, itens incompletos são reatribuídos à próxima milestone antes de fechar a atual. Ao publicar uma versão patch, feche a milestone correspondente e crie a próxima (`0.1.24`, `0.1.25`); para a próxima minor, repita o ciclo com `0.3` depois de publicar `0.2`.

## Exceção da release PR

A validação de milestone no workflow de PRs se aplica a mudanças de código. Uma PR automatizada do Release Please é isenta somente quando o head pertence ao próprio repositório, usa o prefixo de branch gerado `release-please--branches--` e possui o label `autorelease: pending`. Título ou label isolados nunca são suficientes para contornar a regra.

## Operação

Para uma correção de manutenção, crie a PR com base `release/0.1`, atribua a milestone patch exata e aguarde todos os checks. Para uma mudança da próxima minor, use base `master` e milestone `0.2`. Se a base ou a milestone estiverem incorretas, corrija-as antes da aprovação; o job `Validate release milestone` bloqueará a PR até que a combinação seja válida.

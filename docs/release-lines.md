# Linhas de release e milestones

Este documento define como o repositório mantém a próxima minor e a linha de manutenção em paralelo.

## Linhas suportadas

O branch `master` recebe a manutenção da linha `0.1.x`. A única exceção é o trabalho VEXT, que usa a milestone `0.2`. A linha `release/0.1` nasce de `v0.1.22`, recebe as correções da série `0.1.x` e usa a mesma milestone contínua.

Cada PR deve apontar para uma dessas bases e ter exatamente uma milestone compatível. A milestone expressa o objetivo de release; ela não substitui revisão, aprovação ou os checks de CI.

## Release Please

O workflow `.github/workflows/release-please.yml` é acionado em ambas as bases e passa `target-branch` explicitamente. A concorrência é separada por branch, e o título gerado identifica a linha (`chore(master): release ...` ou `chore(release/0.1): release ...`). Git isola o manifest, a configuração e o changelog em cada branch; não são necessários arquivos duplicados com nomes diferentes.

O manifest de `master` continua no histórico da linha de manutenção e o manifest de `release/0.1` começa em `0.1.22`. Assim, commits `fix:` em qualquer PR com milestone `0.1.x` podem gerar o próximo patch sem misturar o trabalho VEXT, enquanto o fluxo VEXT prepara `0.2.0` independentemente.

Tags `v0.1.x` e `v0.2.x` passam pelo mesmo pipeline de entrega porque ambas são linhas suportadas. Se uma linha deixar de ser estável, a política de publicação deve ser revisada antes de desativá-la.

## Propagação e feature freeze

Uma correção que existe em `release/0.1` deve ser encaminhada explicitamente para `master` por forward-merge ou cherry-pick. Mudanças de `master` nunca são backportadas automaticamente para a linha estável. O PR de propagação recebe `0.1.x`, exceto quando for parte do fluxo VEXT.

Durante o feature freeze, itens incompletos permanecem em `0.1.x` até o próximo patch. O trabalho VEXT continua em `0.2` até a publicação da minor.

## Exceção da release PR

A validação de milestone no workflow de PRs se aplica a mudanças de código. Uma PR automatizada do Release Please é isenta somente quando o head pertence ao próprio repositório, usa o prefixo de branch gerado `release-please--branches--` e possui o label `autorelease: pending`. Título ou label isolados nunca são suficientes para contornar a regra.

## Operação

Para uma correção de manutenção, atribua `0.1.x`, independentemente de a PR usar `master` ou `release/0.1`. Para VEXT, use `0.2`. Se a milestone estiver incorreta, corrija-a antes da aprovação; o job `Validate release milestone` bloqueará a PR até que a combinação seja válida.

Descreva o comportamento alterado e o motivo da mudança nesta PR.

## Linha de release

Escolha uma base e uma milestone antes de solicitar revisão:

- `master` + milestone `0.2`: desenvolvimento da próxima minor.
- `release/0.1` + milestone `0.1.x` exata: manutenção da série `0.1`.

O CI exige exatamente uma milestone compatível com a base. PRs de propagação da linha estável para `master` devem usar `0.2` e explicar a referência de origem.

## Checklist

- [ ] A base corresponde à linha de release pretendida.
- [ ] A milestone de release está atribuída e é compatível com a base.
- [ ] A mudança não depende de uma versão futura não documentada.

Descreva o comportamento alterado e o motivo da mudança nesta PR.

## Linha de release

Escolha uma base e uma milestone antes de solicitar revisão:

- `release/0.3` + milestone `0.3`: desenvolvimento da próxima minor.
- `master` + milestone `0.2.x`: manutenção da série `0.2`.

O CI exige exatamente uma milestone compatível com a base. PRs de sincronização entre `master` e `release/0.3` devem explicar a referência de origem e usar a milestone correspondente à branch de destino.

## Checklist

- [ ] A base corresponde à linha de release pretendida.
- [ ] A milestone de release está atribuída e é compatível com a base.
- [ ] A mudança não depende de uma versão futura não documentada.

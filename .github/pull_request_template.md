Descreva o comportamento alterado e o motivo da mudança nesta PR.

## Issue da spec

Uma linha só, com `Closes #N`, `Refs #N` ou `Spec: #N`. Feature (`feat:`) exige a issue com a spec; correção pode dispensar quando for pequena e óbvia (veja o CONTRIBUTING).

## Linha de release

Escolha uma base e uma milestone antes de solicitar revisão (configuração em `.github/release-lines.json`):

- `release/0.4` + milestone `0.4`: desenvolvimento da próxima minor.
- `master` + milestone `0.3.x`: manutenção da série `0.3`.

O CI exige exatamente uma milestone compatível com a base. PRs de sincronização entre as linhas devem explicar a referência de origem e usar a milestone correspondente à branch de destino.

## Checklist

- [ ] Se for feature, a issue com a spec está vinculada e foi confirmada por um mantenedor.
- [ ] A base corresponde à linha de release pretendida.
- [ ] A milestone de release está atribuída e é compatível com a base.
- [ ] A mudança não depende de uma versão futura não documentada.
- [ ] Contribuição de terceiros: o aceite do CLA está registrado.

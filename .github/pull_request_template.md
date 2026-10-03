Descreva o comportamento alterado e o motivo da mudanÃ§a nesta PR.

## Linha de release

Escolha uma base e uma milestone antes de solicitar revisÃ£o:

- `release/0.3` + milestone `0.3`: desenvolvimento da prÃ³xima minor.
- `master` + milestone `0.2.x` exata: manutenÃ§Ã£o da sÃ©rie `0.3`.

O CI exige exatamente uma milestone compatÃ­vel com a base. PRs de sincronizaÃ§Ã£o entre `master` e `release/0.3` devem explicar a referÃªncia de origem e usar a milestone correspondente Ã  branch de destino.

## Checklist

- [ ] A base corresponde Ã  linha de release pretendida.
- [ ] A milestone de release estÃ¡ atribuÃ­da e Ã© compatÃ­vel com a base.
- [ ] A mudanÃ§a nÃ£o depende de uma versÃ£o futura nÃ£o documentada.

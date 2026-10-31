---
title: "Tutorial: publicação e rollback"
weight: 4
---

Gere um artefato assinado e instale-o com uma chave confiável do publisher.

## Gerar e assinar

```bash
vext build ./hello-extension --output ./dist/hello.vext --signing-key ./publisher.key
vext verify ./dist/hello.vext --public-key ./publisher.pub
```

Mantenha chaves privadas fora do repositório. A assinatura cobre o manifesto canônico e o registro de integridade.

## Instalar e reverter

```bash
vext install ./dist/hello.vext --root ~/.vectora/extensions --public-key ./publisher.pub
vext rollback hello.tool 1.0.0 --root ~/.vectora/extensions --public-key ./publisher.pub
```

Artefatos unsigned são somente para desenvolvimento local e exigem `--allow-unsigned` explícito.

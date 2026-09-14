---
title: "Tutorial: publishing and rollback"
weight: 4
---

Build a signed artifact and install it with a trusted publisher key.

## Build and sign

```bash
vext build ./hello-extension --output ./dist/hello.vext --signing-key ./publisher.key
vext verify ./dist/hello.vext --public-key ./publisher.pub
```

Keep private keys outside the repository. The signature covers the canonical manifest and integrity record.

## Install and roll back

```bash
vext install ./dist/hello.vext --root ~/.vectora/extensions --public-key ./publisher.pub
vext rollback hello.tool 1.0.0 --root ~/.vectora/extensions --public-key ./publisher.pub
```

Unsigned artifacts are for local development only and require explicit `--allow-unsigned`.

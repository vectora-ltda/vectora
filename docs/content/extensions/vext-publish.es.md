---
title: "Tutorial: publicación y rollback"
weight: 4
---

Genera un artefacto firmado e instálalo con una clave confiable del publisher.

## Generar y firmar

```bash
vext build ./hello-extension --output ./dist/hello.vext --signing-key ./publisher.key
vext verify ./dist/hello.vext --public-key ./publisher.pub
```

Mantén las claves privadas fuera del repositorio. La firma cubre el manifiesto canónico y el registro de integridad.

## Instalar y revertir

```bash
vext install ./dist/hello.vext --root ~/.vectora/extensions --public-key ./publisher.pub
vext rollback hello.tool 1.0.0 --root ~/.vectora/extensions --public-key ./publisher.pub
```

Los artefactos unsigned son solo para desarrollo local y requieren `--allow-unsigned` explícito.

---
title: "Tutorial: Backend Python"
weight: 2
---

Crea un backend que responda a un método JSON-RPC de VEXT.

## Crear e implementar

```bash
vext init ./hello-extension
cd hello-extension
```

Edita `main.py` con una función `handle(method, params)` y declara solo las capacidades necesarias.

## Validar e instalar

```bash
vext dev .
vext build . --output ../hello.vext
vext install ../hello.vext --root ~/.vectora/extensions --allow-unsigned
```

Usa una clave confiable antes de distribuir el artefacto.

---
title: "Tutorial: Frontend TypeScript"
weight: 3
---

Monta un bundle frontend mediante el sandbox iframe de VEXT.

## Instalar y montar

```bash
npm install @vectora/extension-sdk
```

Usa `mountSandboxedExtension` desde `@vectora/extension-sdk/sandbox`, espera `mounted.context.request(...)` y llama `mounted.dispose()` al retirar la contribución. El host debe validar métodos, parámetros, capacidades y respuestas.

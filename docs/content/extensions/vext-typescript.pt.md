---
title: "Tutorial: Frontend TypeScript"
weight: 3
---

Monte um bundle frontend pela bridge sandbox do iframe VEXT.

## Instalar e montar

```bash
npm install @vectora/extension-sdk
```

Use `mountSandboxedExtension` de `@vectora/extension-sdk/sandbox`, aguarde `mounted.context.request(...)` e chame `mounted.dispose()` ao remover a contribuição. O host deve validar métodos, parâmetros, capabilities e respostas.

---
title: "Tutorial: TypeScript frontend"
weight: 3
---

Mount a frontend bundle through the VEXT iframe sandbox.

## Install and mount

```bash
npm install @vectora/extension-sdk
```

```ts
import { mountSandboxedExtension } from "@vectora/extension-sdk/sandbox";

const mounted = mountSandboxedExtension(
  container,
  "/extensions/hello/index.html",
  manifest,
  (method, params) => hostRequest(method, params),
);
await mounted.context.request("greet", { name: "Vectora" });
mounted.dispose();
```

The host must validate methods, parameters, capabilities and responses. The iframe has `sandbox="allow-scripts"` and no privileged DOM access.

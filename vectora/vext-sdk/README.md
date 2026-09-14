# Vectora VEXT SDK

The VEXT SDK provides TypeScript contracts and a browser sandbox bridge for Vectora extensions. It is currently developed inside the Vectora monorepo and is published as the standalone npm package `@vectora/extension-sdk`.

[Português](README.pt-BR.md) · [Español](README.es-MX.md)

## Install

Install the package from npm:

```bash
npm install @vectora/extension-sdk
```

## Exports

The root export contains manifest, capability, JSON-RPC and context types. The `@vectora/extension-sdk/sandbox` export contains `mountSandboxedExtension` for loading a frontend bundle in an iframe with `sandbox="allow-scripts"`.

## Development

Run `npm run check` from this directory to typecheck and build the package. The package follows the [VEXT protocol](../../docs/content/extensions/vext.en.md).

## Support and license

Read [CONTRIBUTING.md](CONTRIBUTING.md) before sending changes. Security reports belong in [SECURITY.md](SECURITY.md). This project is available under the Apache License 2.0; see [LICENSE](LICENSE).

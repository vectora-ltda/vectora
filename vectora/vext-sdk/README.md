# Vectora VEXT SDK

The VEXT SDK provides TypeScript contracts and a browser sandbox bridge for extensions. It is developed as an internal monorepo and publishes `@vext/sdk` plus shared `@vext/*` packages. It remains embedded in Vectora while the host and packages evolve together, and can later be split into its own repository without changing package names.

[Português](README.pt-BR.md) · [Español](README.es-MX.md)

## Install

Install the package from npm:

```bash
npm install @vext/sdk
```

## Packages

| Package             | Responsibility                                               |
| ------------------- | ------------------------------------------------------------ |
| `@vext/json-rpc`    | JSON-RPC wire types, validation and size limits              |
| `@vext/sdk`         | Manifest types, extension context and browser sandbox bridge |
| `@vext/diagnostics` | Diagnostics and source locations                             |
| `@vext/process`     | Process runner contracts, timeout and output safeguards      |
| `@vext/workspace`   | Workspace file abstraction and in-memory implementation      |
| `@vext/auth`        | Token lifecycle and scope checks                             |
| `@vext/cache`       | Namespaced cache with TTL support                            |
| `@vext/test`        | Host test double and request assertions                      |
| `@vext/ui`          | Host-neutral command and theme primitives                    |

All runtime protocols are defined in this workspace first and consumed by Vectora host adapters.

## Exports

The root export contains manifest, capability, JSON-RPC and context types. The `@vext/sdk/sandbox` export contains `mountSandboxedExtension` for loading a frontend bundle in an iframe with `sandbox="allow-scripts"`.

## Development

Run `npm install` once and then `npm run check` from this directory to typecheck and build every package. Each package emits to its own ignored `dist/` directory and can be published independently with `npm publish --workspace=@vext/<name>`. The package follows the [VEXT protocol](../../docs/content/extensions/vext.en.md).

## Support and license

Read [CONTRIBUTING.md](CONTRIBUTING.md) before sending changes. Security reports belong in [SECURITY.md](SECURITY.md). This project is available under the Apache License 2.0; see [LICENSE](LICENSE).

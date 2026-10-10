---
title: VEXT format and security
weight: 1
---

VEXT is Vectora's versioned extension format. A `.vext` file is a deterministic package containing `vectora-extension.json`, production payloads, SHA-256 integrity metadata, an optional Ed25519 signature, and SBOM metadata.

## Manifest

The manifest declares `id`, `publisher`, `name`, SemVer `version`, `api_version`, `protocol_version`, runtime, entrypoints, capabilities, and supported platforms. Runtime is `python`, `node`, or `none`. Paths are relative POSIX paths; traversal, symlinks, duplicate members, and metadata collisions are rejected.

## Build and verify

```bash
vext build ./my-extension --output ./dist/my-extension.vext
vext validate ./dist/my-extension.vext
vext inspect ./dist/my-extension.vext
vext verify ./dist/my-extension.vext
```

The builder excludes development dependencies, enforces package and decompressed-size limits, and publishes atomically. Verification compares every regular ZIP member with the integrity record before any code is loaded.

## Trust and installation

Production requires a trusted publisher key. Development can opt into unsigned artifacts explicitly:

```bash
vext install ./dist/my-extension.vext --root ~/.vectora/extensions --allow-unsigned
vext install ./dist/my-extension.vext --root ~/.vectora/extensions --public-key publisher.pub
```

Versions are immutable and activation is atomic. Rollback uses the same trust policy and lock. Revoked keys must not be installed, rolled back, or started.

## Runtime and sandbox

Adapters run out of process over newline-delimited JSON-RPC 2.0. The host correlates IDs, limits message size, enforces timeouts, and validates result/error responses. Linux production uses Bubblewrap with a private writable root, dropped capabilities, isolated namespaces, and no network unless requested. Unsupported production sandboxing fails closed.

## Frontend bridge

`@vectora/extension-sdk/sandbox` mounts a frontend bundle in an iframe with `sandbox="allow-scripts"`. The bridge validates the iframe source, correlates requests, and rejects pending calls on `dispose`. Host code remains responsible for method, capability, and payload validation.

# Official VEXT extensions

This workspace contains the first-party extensions distributed through the Vectora Library. Each extension is a full-stack package with a frontend entrypoint for the UI and a backend entrypoint for integrations and privileged operations.

Packages are kept in this repository while the ecosystem is developed. They are designed to be split into an independent open-source repository later without changing their manifests or runtime contracts.

## Development

Run `npm install` and then `npm run check` from this directory. Every package must provide a deterministic build, tests, a `vectora-extension.json` manifest, and both frontend and backend entrypoints. Published `.vext` artifacts are signed and uploaded through the VEXT registry.

## Security

Backend code must declare capabilities in its manifest and validate all external input. Frontends communicate with the host through the VEXT protocol and must not access host APIs directly. See the Apache-2.0 `LICENSE` file for reuse terms.

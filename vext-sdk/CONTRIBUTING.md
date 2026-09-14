# Contributing

Thanks for helping improve the Vectora VEXT SDK.

## Workflow

Changes are made in the Vectora monorepo while the SDK is prepared for a future split. Open an issue before large API changes, keep commits in Conventional Commits format, and include tests or a clear validation note.

## Local checks

Run `npm run check` inside `vext-sdk`. Do not commit `dist`, credentials, tokens, or generated private data. Keep public APIs backwards compatible unless the change is documented.

## Pull requests

Describe the user-visible behavior, security impact, and migration notes. Reviewers must be able to run the documented commands on a clean checkout.

[README](README.md) · [Security](SECURITY.md)

---
title: Vectora Documentation
type: docs
cascade:
  type: docs
sidebar:
  open: true
---

**Vectora** is an AI-assisted development workspace. It brings chat, files, terminal, Git, browser, project search, and automation tools together while keeping the user in control of the workspace, permissions, and connected providers.

The Vectora application is **open source under AGPL-3.0**. See the [license](https://github.com/vectora-ltda/vectora/blob/master/LICENSE), [repository](https://github.com/vectora-ltda/vectora), [contribution guide](https://github.com/vectora-ltda/vectora/blob/master/CONTRIBUTING.md), and [public issues](https://github.com/vectora-ltda/vectora/issues). Individual local use is free; a Pro entitlement enables paid features and services. Third-party providers may charge for their own usage. The entitlement does not change the code license.

## Start here

| I want to...                             | Go to                                          |
| ----------------------------------------- | ----------------------------------------------- |
| Install Vectora                           | [Installation](./getting-started/installation)   |
| Run it in 5 minutes                       | [Quick start](./getting-started/quick-start) |
| Understand the RAG pipeline               | [RAG & Context Graph](./concepts/rag)          |
| Connect an MCP server (as a client)       | [MCP client](./reference/mcp-client)         |
| See all CLI commands                      | [CLI reference](./reference/cli)             |
| Deploy on a server                        | [Requirements](./deployment/requirements)      |
| Understand auth, secrets, and BYOK        | [Security](./security/authentication)          |
| Explore version 0.1.24                     | [What's new in 0.1.24](./guides/release-0.1.24) |

## Data and models

Vectora can run locally or on a machine you administer. Local models can keep inference in your environment; when you configure a remote provider, the required requests are sent to that provider. Before connecting services, read [BYOK and privacy](./security/byok-privacy) and check what data each integration receives.

The default mode uses SQLite and LanceDB for local data and indexes. The `complete` configuration connects external storage services that you operate. See [RAG and Context Graph](./concepts/rag), [storage](./concepts/storage), and [deployment requirements](./deployment/requirements).

## About this documentation

This site contains public installation, configuration, usage, security, and reference guides. It follows the stable `0.1.x` line; see the [repository changelog](https://github.com/vectora-ltda/vectora/blob/master/CHANGELOG.md) for the complete version history. Features may depend on configuration, operating system, provider, or external service. A skipped automated test does not establish operational availability.

## Limits of version 0.1.24

- **Sandbox:** isolation depends on the operating system, available worker, and workspace configuration. Windows uses WSL2; if required `bwrap` is unavailable, the fallback does not isolate the filesystem or network. Read [Sandbox](./concepts/sandbox) before trusting automated execution.
- **External providers:** requests to LLMs, embedding models, rerankers, and search providers may send data to the selected service. A local installation does not mean a remote provider processes data locally.
- **Validation:** unit tests, cassette replay, and skipped live tests cover different scopes. See [Verification and Evaluation Lab](./guides/verification-lab) before interpreting results.

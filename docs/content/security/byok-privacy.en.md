---
title: BYOK & Privacy
weight: 4
---

## What self-hosted means

Vectora's backend and local data live on the machine or infrastructure you operate. This does not mean all processing happens offline or that no data leaves that infrastructure. When you use an external model or integration, Vectora sends that service the data needed for the requested operation — for example, your message and selected context for an LLM call.

Vectora may also communicate with auxiliary services for features such as authentication, licensing, updates, and integrations you enable. Check your instance settings, active endpoints, and the policies of the services you choose. Do not send confidential data to an external provider unless you are authorized to do so.

## BYOK (Bring Your Own Key)

You configure the providers and credentials you want to use. The local backend stores keys according to the installation's secrets mechanism; this does not prevent a provider from receiving a request when you select it. Provider terms govern costs, retention, training, and data use. Local models can reduce third-party transmission, but network-dependent features such as web search and remote MCP integrations still send the data they need.

## Protecting stored data

Vectora stores sessions, settings, and indexes on infrastructure you administer. The operator of that machine can access local data. Protect the operating system, backups, volumes, and credentials; self-hosting is not end-to-end encryption and does not protect data from the server administrator.

## GDPR and data protection

Instance operators should assess their legal basis and each provider's or integration's policies before sending personal data. Vectora does not replace that assessment or the operator's obligations.

## Report a vulnerability

Use the channel in the [security policy](https://github.com/vectora-ltda/vectora/blob/master/SECURITY.md); do not publish exploitable details in a public issue.

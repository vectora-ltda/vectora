---
title: Security
weight: 3
---

**Does my data leave my server?**
It depends on the features you use. External providers receive data needed for requests you send; remote integrations receive data needed for their operations. Vectora may also use auxiliary services for authentication, licensing, updates, or enabled features. See [BYOK & Privacy](../../security/byok-privacy).

**Does Vectora have end-to-end encryption?**
No. The backend must read content to run tools, index the workspace, and send context to the selected model. Protect the machine and backups that store your data.

**How does Vectora handle prompt injection?**
Content from files, pages, and tool results is treated as untrusted input. The agent also applies policies and HITL approvals to operations according to the permission mode and workspace configuration. These controls reduce risk, but do not make it safe to execute external instructions without review.

**Can I restrict what the agent can do?**
Yes. Configure HITL mode, tool policies, workspace trust, and sandbox controls. The availability of each protection depends on platform and configuration. See the [security](../../security/) and [workspace](../../getting-started/first-workspace) guides.

**Where do I report a vulnerability?**
Follow the repository's [security policy](https://github.com/vectora-ltda/vectora/blob/master/SECURITY.md) instead of publishing details in an issue.

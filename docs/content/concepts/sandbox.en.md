---
title: Sandbox
weight: 5
---

Sandbox is Vectora's isolation layer for the agent's terminal and filesystem access. It follows the design of Fabio Akita's [ai-jail](https://github.com/akitaonrails/ai-jail) — the agent's actions run *inside* the jail, not behind a single sandboxed function call — adapted to Vectora's architecture: a single long-running backend process serving many concurrent workspaces, rather than one process per invocation.

## Threat model

Without a sandbox, `terminal`, `file_write`, and `file_edit` operate directly in the backend process — the agent (or a prompt injection) can read `~/.ssh`, exfiltrate secrets, or run destructive commands with the same privileges as the backend itself. Sandbox exists to bound that blast radius to a workspace's declared `rw_paths`/`ro_paths`, without giving up file and terminal access entirely (that would defeat the point of an autonomous coding agent).

## Activation per workspace

Sandbox is configured per workspace. If the workspace has **no** `vectora.toml`, Vectora turns the sandbox on automatically when the environment supports native isolation (`bwrap` on Linux, WSL2 on Windows); without that support, and always on macOS, it stays off. If the workspace has a `vectora.toml`, the `[sandbox]` section applies:

```toml
[sandbox]
enabled = true
backend = "local"          # local (bwrap, Linux) | docker | ssh | modal
rw_paths = ["."]           # writable inside the jail
ro_paths = []               # read-only inside the jail
mask = [".env", "**/*.pem", "**/.ssh/**", "**/.aws/**"]
no_gpu = true
lockdown = false            # true: fail-closed on any policy ambiguity
```

If `vectora.toml` exists without a `[sandbox]` section, the sandbox stays off and the workspace behaves exactly as before this feature existed. Malformed TOML or an invalid field, once `[sandbox]` is present, fails **closed** (locked down) instead of silently running unprotected.

For worker network control (`lockdown` and `allow_tcp_ports`) and the egress policy for network tools, see [Egress policies](../../guides/egress-policies).

## How it works: one worker per workspace

Instead of spawning a new sandboxed process for every tool call, Vectora keeps a single persistent jailed worker process alive per workspace with `[sandbox]` enabled — born on the first sandboxable action, killed when the workspace goes idle. `file_write`, `file_edit`, and one-shot `terminal` commands route through this worker via a small JSON-lines protocol (`exec`/`read_file`/`write_file`).

On the `local` backend (Linux, or Windows through WSL2), the worker runs under `bwrap` when available (Bubblewrap) with:

- Filesystem namespacing restricted to `rw_paths`/`ro_paths`.
- Masked paths (`mask`) never exposed inside the jail, even if they fall under an allowed path.
- A real seccomp-BPF filter (via `libseccomp`) denying a fixed list of dangerous syscalls (`ptrace`, `mount`, `unshare`, `bpf`, kernel module loading, etc.) — if `libseccomp` isn't installed on the host, the worker still runs under namespace isolation, just without the syscall filter, and this degradation is logged.

On Windows, automatic activation checks for an eligible WSL2 distribution, not for `bwrap` inside it. If `bwrap` is missing, the worker falls back to a native soft process with no filesystem or network isolation, even with `lockdown = true`. macOS does not automatically enable isolation: the persistent local worker attempts `bwrap`, not Seatbelt (`sandbox-exec`).

Soft mode does not qualify for a sandbox HITL exemption. Until a live worker with effective isolation has been confirmed, ordinary approval rules apply; after HITL relies on native isolation, a soft replacement is refused.

Other backends (`docker`, `ssh`, `modal`) run the same worker model against their respective isolation primitive; `docker` additionally denies network access under `lockdown`.

## Known limitation: interactive terminal

The one-shot `terminal` tool call and file tools are sandboxed today. An **interactive** terminal session (the Terminal tab, PTY-backed) is not yet routed through the jailed worker — starting an interactive terminal on a sandboxed workspace returns a clear error rather than silently falling back to an unsandboxed shell. This is scoped for a future iteration.

## See also

- [Using the Workbench](../../guides/using-the-workbench) — the Terminal and Files tabs in practice
- [Sessions & Workspaces](../sessions-and-workspaces) — how a workspace is trusted and scoped

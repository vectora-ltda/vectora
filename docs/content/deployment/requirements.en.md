---
title: Requirements
weight: 1
---

## Desktop (personal / small team use)

| Item     | Minimum                                                                |
| -------- | ---------------------------------------------------------------------- |
| OS       | Windows x64/ARM64; macOS Apple Silicon; Linux x64/ARM64 (Ubuntu 20.04+/Debian 11+/Fedora 35+) |
| RAM      | 4 GB (8 GB recommended)                                                |
| Disk     | 2 GB free for the app + space for your local LanceDB                   |
| Internet | Needed for LLM/embeddings/web search API calls                         |

## Server (VPS / shared instance)

| Item | Minimum      | Recommended  |
| ---- | ------------ | ------------ |
| CPU  | 2 vCPU       | 4+ vCPU      |
| RAM  | 4 GB         | 8 GB+        |
| Disk | 20 GB        | 50 GB SSD    |
| OS   | 64-bit Linux | Ubuntu 24.04 |

**Complete** mode (Postgres + Qdrant + Redis) adds those three services' requirements on top — size according to expected data volume.

## Network

- Configurable HTTP port (default `8080`) — the web chat and its internal endpoints share the same port.
- TLS isn't terminated by Vectora itself — use a reverse proxy (Nginx, Caddy, Traefik) in front in production.
- No additional TCP port is needed — the desktop talks to the local backend via IPC (named pipe/unix socket), not TCP.

## Next step

→ [Docker (optional infrastructure)](../docker)

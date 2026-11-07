---
title: Requisitos
weight: 1
---

## Desktop (uso pessoal/pequeno time)

| Item     | Mínimo                                                                 |
| -------- | ---------------------------------------------------------------------- |
| SO       | Windows x64/ARM64; macOS Apple Silicon; Linux x64/ARM64 (Ubuntu 20.04+/Debian 11+/Fedora 35+) |
| RAM      | 4 GB (8 GB recomendado)                                                |
| Disco    | 2 GB livres pro app + espaço pro seu LanceDB local                     |
| Internet | Necessária pras chamadas de API de LLM/embeddings/busca web            |

## Servidor (VPS / instância compartilhada)

| Item  | Mínimo       | Recomendado  |
| ----- | ------------ | ------------ |
| CPU   | 2 vCPU       | 4+ vCPU      |
| RAM   | 4 GB         | 8 GB+        |
| Disco | 20 GB        | 50 GB SSD    |
| SO    | Linux 64-bit | Ubuntu 24.04 |

Modo **complete** (Postgres + Qdrant + Redis) soma os requisitos desses três serviços por cima — dimensione conforme o volume de dados esperado.

## Rede

- Porta HTTP configurável (padrão `8080`) — o chat web e seus endpoints internos compartilham a mesma porta.
- TLS não é terminado pelo próprio Vectora — use um reverse proxy (Nginx, Caddy, Traefik) na frente em produção.
- Nenhuma porta TCP adicional é necessária — o desktop fala com o backend local via IPC (named pipe/unix socket), não TCP.

## Próximo passo

→ [Docker (infraestrutura opcional)](../docker)

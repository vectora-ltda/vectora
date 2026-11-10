---
title: Requirements
weight: 1
---

## Escritorio (uso personal / equipo pequeño)

| Ítem     | Mínimo                                                                  |
| -------- | -------------------------------------------------------------------------- |
| SO       | Windows x64/ARM64; macOS Apple Silicon; Linux x64/ARM64 (Ubuntu 20.04+/Debian 11+/Fedora 35+) |
| RAM      | 4 GB (8 GB recomendado)                                                   |
| Disco    | 2 GB libres para la app + espacio para tu LanceDB local                   |
| Internet | Necesario para las llamadas a las APIs de LLM/embeddings/búsqueda web    |

## Servidor (VPS / instancia compartida)

| Ítem  | Mínimo        | Recomendado  |
| ----- | -------------- | ------------- |
| CPU   | 2 vCPU         | 4+ vCPU       |
| RAM   | 4 GB           | 8 GB+         |
| Disco | 20 GB          | 50 GB SSD     |
| SO    | Linux de 64-bit | Ubuntu 24.04  |

El modo **completo** (Postgres + Qdrant + Redis) agrega los requisitos de esos tres servicios encima — dimensiona según el volumen de datos esperado.

## Red

- Puerto HTTP configurable (por defecto `8080`) — el chat web y sus endpoints internos comparten el mismo puerto.
- Vectora no termina TLS por sí mismo — usa un reverse proxy (Nginx, Caddy, Traefik) al frente en producción.
- No se necesita ningún puerto TCP adicional — el escritorio habla con el backend local vía IPC (named pipe/unix socket), no TCP.

## Siguiente paso

→ [Docker (infraestructura opcional)](../docker)

---
title: Políticas de egress
weight: 10
---

Esta guía explica cómo Vectora controla el tráfico de salida del agente: la política de egress, que se aplica a las herramientas de red, y el aislamiento de red del sandbox, que se aplica a los comandos ejecutados en el worker.

> **Estado:** estas capacidades forman parte de la línea 0.3. Esta página no declara concluidas las pruebas live que todavía aparecen como `skipped`.

## Requisitos previos

- Un workspace abierto en Vectora.
- Para el aislamiento de red de los comandos: el sandbox activo en el workspace (consulta [Sandbox](../../concepts/sandbox)).

## Dos capas

| Capa | Dónde se aplica | Cómo se configura |
| --- | --- | --- |
| Política de egress | Herramientas HTTP y web (búsqueda, fetch, crawl, map) y navegación del Browser Workbench | Configuración de runtime (`egress_policy`) |
| Red del sandbox | Comandos y archivos ejecutados en el worker aislado | Sección `[sandbox]` de `vectora.toml` |

La allowlist de servidores MCP y las preaprobaciones de herramientas no son políticas de egress. Controlan qué servidores y acciones se pueden usar, no hacia dónde va el tráfico.

## Política de egress

La política se lee de la configuración de runtime, mediante la clave `egress_policy:<workspace_id>` o la clave `egress_policy` (un objeto por workspace o un objeto único). En esta línea no hay pantalla ni endpoint dedicado para editarla.

| Campo | Por defecto | Efecto |
| --- | --- | --- |
| `block_private_ips` | `true` | Bloquea destinos que resuelven a IP privada, loopback o no pública |
| `allowed_hosts` | vacío | Si se define, solo se permiten esos hosts |
| `denied_hosts` | vacío | Hosts siempre bloqueados |
| `allowed_ports` | `[80, 443]` | Puertos permitidos; una lista vacía no restringe puertos |
| `allow_localhost_dev_ports` | vacío | Puertos de loopback permitidos para servidores de desarrollo |

Cómo se evalúa la política:

1. Solo se aceptan URLs `http` y `https`.
2. `denied_hosts` se comprueba antes que `allowed_hosts`. Un patrón `*.ejemplo.com` cubre `ejemplo.com` y todos sus subdominios.
3. El host se resuelve por DNS y se comprueba cada dirección resuelta. Esto protege contra SSRF y DNS rebinding.
4. El puerto debe estar en `allowed_ports`, o en `allow_localhost_dev_ports` cuando el destino es loopback.

Los subagentes heredan la política de la ejecución que los creó.

## Red del sandbox

Cuando el sandbox está activo, la sección `[sandbox]` controla la red de los comandos:

```toml
[sandbox]
enabled = true
backend = "local"
lockdown = false
allow_tcp_ports = [443]
```

- `lockdown = true` niega toda la red del worker (`--unshare-net`).
- `allow_tcp_ports` niega todo el TCP de salida excepto los puertos listados. La restricción usa Landlock ABI V4 y requiere Linux con kernel 6.5 o superior.
- Con `allow_tcp_ports` vacío (el valor por defecto), el sandbox no restringe la red por puerto.

## Limitaciones

- El backend `local` del sandbox (bubblewrap y Landlock) funciona en Linux; en Windows depende de WSL2.
- En kernels sin Landlock ABI V4, `allow_tcp_ports` no se aplica; el worker registra que la restricción no está vigente.
- Vectora identifica qué backends de sandbox pueden aplicar restricciones de red, pero todavía no rechaza la ejecución cuando el backend elegido no puede.
- La política de egress cubre las herramientas listadas arriba; no supongas que otras rutas de red usan los mismos controles.

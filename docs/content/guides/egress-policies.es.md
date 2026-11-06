---
title: Políticas de egress
weight: 10
---

Esta guía explica cómo Vectora controla el tráfico de salida del agente: la política de egress, que se aplica a las herramientas de red, y el aislamiento de red del sandbox, que se aplica a los comandos ejecutados en el worker.

> **Estado:** estas capacidades están incluidas en la versión 0.1.24. Las pruebas live marcadas como `skipped` no demuestran el funcionamiento en un entorno real.

## Requisitos previos

- Un workspace abierto en Vectora.
- Para el aislamiento de red de los comandos: el sandbox activo en el workspace (consulta [Sandbox](../../concepts/sandbox)).

Configura cada capa según las operaciones que necesites restringir.

## Configuración

El tráfico de salida se controla con dos capas independientes, cada una configurada en un lugar distinto.

### Dos capas

| Capa | Dónde se aplica | Cómo se configura |
| --- | --- | --- |
| Política de egress | URLs iniciales de fetch, crawl y map y navegación del Browser Workbench; consulta abajo las exclusiones de redirecciones Chromium y búsqueda | Configuración de runtime (`egress_policy`) |
| Red del sandbox | Comandos y archivos ejecutados en el worker aislado | Sección `[sandbox]` de `vectora.toml` |

La allowlist de servidores MCP y las preaprobaciones de herramientas no son políticas de egress. Controlan qué servidores y acciones se pueden usar, no hacia dónde va el tráfico.

### Política de egress

La política se lee de la configuración de runtime: `egress_policy:<workspace_id>` contiene directamente la política del workspace; como alternativa, `egress_policy` asocia IDs de workspace con objetos de política, por ejemplo `{"mi-workspace": {"allowed_hosts": ["example.com"]}}`. Para un workspace, un objeto único sin esa clave en `egress_policy` se ignora y se aplica la política predeterminada. No hay pantalla ni endpoint dedicado para editarla.

| Campo | Por defecto | Efecto |
| --- | --- | --- |
| `block_private_ips` | `true` | Bloquea destinos que resuelven a IP privada, loopback o no pública |
| `allowed_hosts` | vacío | Si se define, solo se permiten esos hosts |
| `denied_hosts` | vacío | Hosts siempre bloqueados |
| `allowed_ports` | `[80, 443]` | Puertos permitidos; una lista vacía no restringe puertos |
| `allow_localhost_dev_ports` | vacío | Puertos de loopback permitidos por la capa de egress; `fetch_url` todavía rechaza loopback mediante su guard SSRF independiente |

Cómo se evalúa la política:

1. Solo se aceptan URLs `http` y `https`.
2. `denied_hosts` se comprueba antes que `allowed_hosts`. Un patrón `*.ejemplo.com` cubre `ejemplo.com` y todos sus subdominios.
3. El host se resuelve por DNS y se comprueba cada dirección devuelta en esa consulta. La ruta HTTP directa fija la IP validada; el fallback Chromium no la fija, por lo que esta comprobación no garantiza protección contra DNS rebinding en esa ruta.
4. El puerto debe estar en `allowed_ports`, o en `allow_localhost_dev_ports` cuando el destino es loopback.

Los subagentes heredan la política de la ejecución que los creó.

### Red del sandbox

Cuando el sandbox está activo, la sección `[sandbox]` controla la red de los comandos:

```toml
[sandbox]
enabled = true
backend = "local"
lockdown = false
allow_tcp_ports = [443]
```

- `lockdown = true` niega toda la red del worker (`--unshare-net`) cuando se ejecuta bajo `bwrap` (Linux, o Windows mediante WSL2).
- `allow_tcp_ports` niega todo el TCP de salida excepto los puertos listados. La restricción usa Landlock ABI V4 y requiere Linux con kernel 6.7 o superior. El ruleset también controla bind TCP, pero solo concede connect: un servidor local no puede hacer bind ni en un puerto listado cuando esta restricción está activa.
- Con `allow_tcp_ports` vacío (el valor por defecto), el sandbox no restringe la red por puerto.

Antes de usar estas capacidades, revisa los límites de plataforma, configuración y validación siguientes.

## Limitaciones

- El backend `local` del sandbox (bubblewrap y Landlock) funciona en Linux; en Windows depende de WSL2. Sin WSL2, o sin `bwrap` en la distribución, el worker pasa a un modo soft que no aplica `--unshare-net`: con `lockdown = true`, los comandos se siguen ejecutando y la red queda abierta.
- En macOS el sandbox no se activa automáticamente, y el worker persistente no usa Seatbelt (`sandbox-exec`).
- En kernels sin Landlock ABI V4, `allow_tcp_ports` no se aplica; el worker registra que la restricción no está vigente.
- Vectora identifica qué backends de sandbox pueden aplicar restricciones de red, pero todavía no rechaza la ejecución cuando el backend elegido no puede.
- La política de egress cubre las herramientas listadas arriba; no supongas que otras rutas de red usan los mismos controles.
- `web_search` envía una consulta, no una URL: las peticiones al proveedor de búsqueda y a la búsqueda de fallback (DuckDuckGo) no pasan por la política de egress.
- `fetch_url` usa Chromium sin `TAVILY_API_KEY` o cuando el proveedor de extracción informa errores de cuota/rate limit, incluso con la clave configurada. En ambos casos se comprueba la URL inicial, pero las redirecciones del navegador no se vuelven a comprobar y la IP de destino no se fija.

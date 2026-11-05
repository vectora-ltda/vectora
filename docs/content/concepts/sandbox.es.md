---
title: Sandbox
weight: 5
---

Sandbox es la capa de aislamiento de Vectora para el acceso del agente a terminal y filesystem. Sigue el diseño de [ai-jail](https://github.com/akitaonrails/ai-jail), de Fabio Akita — las acciones del agente corren *dentro* de la jaula, no detrás de una única llamada de función sandboxed — adaptado a la arquitectura de Vectora: un único proceso de backend de larga duración sirviendo varios espacios de trabajo simultáneos, en vez de un proceso por invocación.

## Modelo de amenaza

Sin sandbox, `terminal`, `file_write` y `file_edit` operan directamente en el proceso del backend — el agente (o una inyección de prompt) puede leer `~/.ssh`, exfiltrar secretos o ejecutar comandos destructivos con los mismos privilegios que el propio backend. El Sandbox existe para limitar ese radio de daño a los `rw_paths`/`ro_paths` declarados de un espacio de trabajo, sin renunciar por completo al acceso a archivos y terminal (eso anularía el propósito de un agente de código autónomo).

## Activación por espacio de trabajo

El Sandbox se configura por espacio de trabajo. Si el espacio de trabajo **no tiene** `vectora.toml`, Vectora activa el sandbox automáticamente cuando el entorno admite aislamiento nativo (`bwrap` en Linux, WSL2 en Windows); sin ese soporte, y siempre en macOS, queda desactivado. Si el espacio de trabajo tiene `vectora.toml`, rige la sección `[sandbox]`:

```toml
[sandbox]
enabled = true
backend = "local"          # local (bwrap, Linux) | docker | ssh | modal
rw_paths = ["."]           # escribibles dentro de la jaula
ro_paths = []               # solo lectura dentro de la jaula
mask = [".env", "**/*.pem", "**/.ssh/**", "**/.aws/**"]
no_gpu = true
lockdown = false            # true: fail-closed ante cualquier ambigüedad de política
```

Si `vectora.toml` existe sin la sección `[sandbox]`, el sandbox queda desactivado y el espacio de trabajo se comporta exactamente como antes de que existiera esta función. Un TOML malformado o un campo inválido, una vez que `[sandbox]` está presente, falla **cerrado** (lockdown) en vez de correr desprotegido silenciosamente.

Para el control de red del worker (`lockdown` y `allow_tcp_ports`) y la política de egress de las herramientas de red, consulta [Políticas de egress](../../guides/egress-policies).

## Cómo funciona: un worker por espacio de trabajo

En vez de generar un proceso sandboxed nuevo en cada llamada de herramienta, Vectora mantiene un único worker enjaulado persistente vivo por espacio de trabajo con `[sandbox]` habilitado — nace en la primera acción sandboxable, se termina cuando el espacio de trabajo queda inactivo. `file_write`, `file_edit` y comandos `terminal` puntuales se enrutan por este worker vía un protocolo JSON-lines pequeño (`exec`/`read_file`/`write_file`).

En el backend `local` (Linux, o Windows mediante WSL2), el worker corre bajo `bwrap` cuando está disponible (Bubblewrap) con:

- Namespacing de filesystem restringido a `rw_paths`/`ro_paths`.
- Rutas enmascaradas (`mask`) nunca expuestas dentro de la jaula, incluso si caen bajo una ruta permitida.
- Un filtro seccomp-BPF real (vía `libseccomp`) que niega una lista fija de syscalls peligrosas (`ptrace`, `mount`, `unshare`, `bpf`, carga de módulos del kernel, etc.) — si `libseccomp` no está instalado en el host, el worker igual corre bajo aislamiento de namespace, solo que sin el filtro de syscalls, y esa degradación queda registrada en el log.

En Windows, la activación automática comprueba una distribución WSL2 elegible, no la presencia de `bwrap` dentro de ella. Si falta `bwrap`, el worker usa un proceso soft nativo sin aislamiento de filesystem ni red, incluso con `lockdown = true`. En macOS no hay activación automática: el worker local persistente intenta usar `bwrap`, no Seatbelt (`sandbox-exec`).

El modo soft no permite una exención de HITL por sandbox. Hasta confirmar un worker activo con aislamiento efectivo, se aplican las reglas normales de aprobación; después de que HITL dependa de ese aislamiento, se rechaza un reemplazo soft.

Los demás backends (`docker`, `ssh`, `modal`) corren el mismo modelo de worker contra su respectivo primitivo de aislamiento; `docker` además niega el acceso a red bajo `lockdown`.

## Limitación conocida: terminal interactiva

La llamada puntual de la herramienta `terminal` y las herramientas de archivo ya están sandboxed hoy. Una sesión de terminal **interactiva** (la pestaña Terminal, basada en PTY) todavía no se enruta por el worker enjaulado — iniciar una terminal interactiva en un espacio de trabajo sandboxed devuelve un error claro en vez de caer silenciosamente a una shell sin sandbox. Está previsto para una iteración futura.

## Ver también

- [Usando el Workbench](../../guides/using-the-workbench) — las pestañas Terminal y Archivos en la práctica
- [Sesiones y Espacios de Trabajo](../sessions-and-workspaces) — cómo se confía y delimita un espacio de trabajo

---
title: Using Settings
weight: 3
---

Todo lo que configuras a través de la UI vive en tres diálogos separados, cada uno con un propósito claro.

## Preferencias

Configuración personal, para tu usuario.

- **General** — tema (sistema/claro/oscuro/preset/personalizado), idioma, system prompt personalizado, orden de fallback de modelos, y colores personalizados (fondo, texto, tarjeta, borde, primario, acento, muted, sidebar, color de burbuja del usuario).

Los presets **Godot Dark** y **Godot Light** aplican la paleta de Godot a todo el shell y sincronizan el tema de Monaco. Incluyen GDScript, escenas `.tscn`, recursos `.tres`, `project.godot`, shaders `.gdshader`/`.gdshaderinc` y las fences `gdscript`, `gd`, `tscn`, `tres`, `godot` y `gdshader` en Markdown y chat. Los demás presets conservan su apariencia y solo usan las reglas de lenguaje Godot en el código.

Esta entrega es léxica: comentarios, cadenas, tipos, keywords, propiedades, referencias de nodos, folding y brackets. No ofrece autocompletado semántico, diagnósticos de la engine ni documentación F1. La paleta aproxima el tema predeterminado de Godot y mezcla previamente los estados del editor para Monaco; los colores de breakpoint y bookmark forman solo parte del contrato de colores.
- **Memoria** — lista de memorias persistentes (pares clave-valor). Agregar, editar en línea, borrar una o limpiar todas, con una línea de tiempo de última actualización.
- **Cuenta** — nombre (editable), email (solo lectura), rol (root/admin/member/viewer).

## Entorno

Configuración de integración y extensibilidad.

- **Envs** — variables de entorno por usuario (enmascaradas en la UI, nunca expuestas en texto plano), sobreescribiendo el env del sistema solo para ti.
- **Skills** — el gestor de skills del agente. Instala vía URL de git o ruta local, verifica el estado de cada skill, elimina. Cada skill es una carpeta con un `SKILL.md` cargado bajo demanda por el deep-agent.
- **Plugins** — servidores MCP externos configurados por el usuario. Soporta transportes `stdio` (comando + args), `sse` y `http`. Incluye un panel de **Política de Herramientas** para controlar qué herramientas de ese servidor están habilitadas.
- **Integraciones** — tarjetas de conexión para servicios externos: API key manual (ej.: algún servicio de terceros) u OAuth (GitHub, GitLab, Google, Slack). Muestra el estado conectado/desconectado y la URL del webhook cuando aplica.

## Administración (root/admin)

Configuración de toda la instancia — solo visible para administradores.

- **Usuarios** — lista con rol, fecha de creación, último inicio de sesión. Cambiar rol, eliminar un usuario. Sección **Invitaciones**: crear una invitación (rol + email opcional + TTL de 1–720h), copiar URL, revocar invitaciones pendientes.
- **Herramientas** — lista de herramientas globales por categoría, con un interruptor de activar/desactivar por herramienta.
- **Carpetas Seguras** — una lista blanca de rutas que requieren aprobación extra incluso después de ser confiables.
- **Sistema** — versión del backend, versión de Python, plataforma, estado de cada servicio, conteo reciente de spans de observabilidad.
- **Configuración** — `allow_public_signup`, el modelo por defecto de la instancia, profundidad máxima de recursión del agente, DSN de la base de datos (solo lectura), token de integración.

## Ver también

- [Seguridad: Autenticación y RBAC](../../security/authentication)
- [Cliente MCP](../../reference/mcp-client) — cómo se conectan los plugins MCP de la pestaña Entorno

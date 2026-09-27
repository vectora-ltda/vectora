---
title: Using the Workbench
weight: 2
---

El workbench es el panel lateral del chat — una franja delgada y siempre visible de íconos, más un panel de contenido que se abre bajo demanda. Es donde trabajas con archivos, git, la terminal y el resto del contexto del proyecto sin salir de la conversación.

## Abrir y cerrar

- Atajo: **Ctrl+\\** (Windows/Linux) o **⌘+\\** (Mac).
- Haz clic en un ícono de la franja lateral para abrir esa pestaña; hacer clic en la pestaña ya activa colapsa el panel.
- El botón **X** en el encabezado del panel cierra la pestaña actual.

## Las 9 pestañas

### Terminal

Un shell real vía PTY (`pywinpty`/`ptyprocess`), renderizado con xterm.js. Múltiples instancias por sesión (pestaña "+" para abrir una nueva), cada una nombrable. Una terminal se abre automáticamente al montar si el workspace es confiable — los workspaces sin confianza no reciben terminal.

### Archivos

El árbol de archivos del workspace. Barra de herramientas con nuevo archivo/carpeta, refrescar y búsqueda en línea. Cada elemento tiene acciones al pasar el mouse: `@` (agrega al contexto del chat), abrir como ventana flotante, renombrar en línea, mover a la papelera (`Del`) o eliminar permanentemente (`Shift+Del`). Los archivos anclados tienen su propia sección arriba. Un botón de historial muestra las versiones de un archivo vía `git log`/`git show`.

### Diff (Git)

Dos sub-pestañas: **Cambios** (archivos modificados/staged/sin seguimiento, con un parche expandible y stage/unstage por archivo) e **Historial** (log de commits, clic para mostrar el diff completo). Modales dedicados para Stash, Worktrees y creación de PR. Selector de rama y un botón de sincronización en la barra de herramientas.

### Plan

Lista los artefactos generados por el agente en la sesión (planes, specs, guías markdown, vía la herramienta `create_artifact`) y una sección "Archivos tocados" con todo lo que el agente ha leído/creado/editado en este hilo.

### Preview

Gestiona los servidores de desarrollo de tu proyecto (lee configuración como `launch.json`). Botones de play/stop/refrescar, un iframe cargando `localhost:<puerto>`, y un formulario para agregar un servidor manualmente si no se detecta configuración.

### Memoria

Una línea de tiempo en vivo de búsquedas RAG y web en progreso, con extractos recuperados (píldoras expandibles, separadas en "base de conocimiento" vs. "resultados web"). Un panel de configuración de RAG te permite ajustar reranker, top_k y proveedor de embedding.

Esta pestaña también tiene el panel **"Lo que aprendí sobre ti"** — una vista de solo lectura de los hechos y skills duraderos que [Remember](../agent-automation) ya guardó por su cuenta, sin que tengas que recordar en qué hilo el agente aprendió algo por primera vez. Los buckets que importas de otros usuarios también viven aquí, dentro de la **Memory Buckets**.

Una búsqueda en la parte superior de la pestaña consulta hechos, skills y buckets RAG a la vez — chips de filtro por tipo restringen el resultado a solo uno de los tres cuando ya sabes qué buscas.

### Tareas

Rutinas en segundo plano para la sesión: tareas programadas (cron) o disparadas por webhook (GitHub/GitLab/Slack). Cada tarea tiene un interruptor de habilitado, un botón "ejecutar ahora" y un log de ejecuciones pasadas con un enlace para abrir el hilo resultante.

### Buscar

Busca en todos los archivos del workspace. El prefijo `r:` habilita el modo regex (ej.: `r:function\s+\w+`). Resultados agrupados por archivo, con vista previa de línea — clic abre el archivo en la posición exacta.

### Context Graph

El grafo de dependencias y conocimiento del workspace — ver [Context Graph](../../concepts/context-graph) para cómo se construye. Aquí disparas el build, sigues el progreso por etapa (AST → semántico → listo) y navegas los god nodes más las preguntas sugeridas.

## Ventanas flotantes

Hacer clic en el ícono "abrir como ventana" de un archivo (pestaña Archivos) abre una **ventana flotante** independiente — arrastrable, redimensionable (vía `react-rnd`), con sus propias pestañas si abres más de un archivo en ella. Minimizar envía la ventana al dock (barra inferior); hacer clic en el dock la restaura. Cada ventana ejecuta el editor Monaco (texto) o un visor de medios (imágenes/video).

## Ver también

- [Usando el chat](../using-the-chat)
- [Flujos de git](../git-workflows)

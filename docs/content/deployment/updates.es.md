---
title: Actualizaciones
weight: 5
---

## Aplicación de escritorio

Vectora Desktop busca actualizaciones en el canal configurado para la instalación. Cuando hay una versión disponible, la aplicación muestra su estado y las notas de release, crea una copia rotativa de los datos locales y descarga el paquete sin instalarlo de inmediato. La instalación se aplica al salir de la aplicación. El usuario puede controlar las comprobaciones automáticas en preferencias e iniciar una comprobación manual.

El comportamiento puede depender del sistema operativo, el formato instalado y el canal publicado. Consulta las [releases de GitHub](https://github.com/vectora-company/vectora/releases) para ver las versiones y artefactos disponibles.

## Ejecuciones desde el código fuente

Una instancia iniciada con `uv run vectora start` no usa el actualizador de Electron. Actualiza el checkout y las dependencias mediante el flujo de desarrollo de [Instalación](../../getting-started/installation).

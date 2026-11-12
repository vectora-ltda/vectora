---
title: Instalación
weight: 2
---

Hay dos formas de usar Vectora: instalar un paquete de escritorio publicado o ejecutar el proyecto desde el código fuente. Los paquetes de escritorio incluyen el backend y el frontend; no necesitas instalar Python ni Node para usar la aplicación.

## Instalar una versión publicada

Abre las [releases de Vectora en GitHub](https://github.com/vectora-company/vectora/releases) y elige un artefacto compatible con tu sistema y arquitectura. La matriz de compilación publicada actualmente cubre:

| Sistema | Arquitectura publicada | Formatos disponibles |
| --- | --- | --- |
| Windows | x64 y ARM64 | `.exe`, `.msi` |
| macOS | Apple Silicon (ARM64) | `.dmg` |
| Linux | x64 y ARM64 | `.AppImage`, `.deb`, `.rpm` |

CI genera estos artefactos; consulta cada release para saber qué archivos se publicaron realmente. La disponibilidad de un paquete no garantiza que todas las distribuciones Linux tengan las mismas dependencias del sistema. En Linux, revisa las dependencias del paquete y los requisitos de tu distribución antes de instalar.

En Windows, ejecuta el instalador y sigue los pasos. En macOS, abre el DMG y arrastra la aplicación a Applications. En Linux, usa el gestor de paquetes para `.deb`/`.rpm`; para AppImage, concede permiso de ejecución antes de abrirlo. La firma y notarización varían según el artefacto; consulta la información de la release.

## Ejecutar desde el código fuente

Usa esta opción para desarrollo o para inspeccionar y modificar el proyecto. Consulta también la [guía de contribución](https://github.com/vectora-company/vectora/blob/master/CONTRIBUTING.md).

**Requisitos:** [uv](https://docs.astral.sh/uv/), Python 3.13, Node.js 26 y pnpm 11.5. En Linux, `dotfiles.sh` detecta la distribución y prepara la toolchain y las dependencias nativas; se detiene en Windows/macOS.

```bash
git clone https://github.com/vectora-company/vectora.git
cd vectora

# ./dotfiles.sh  # opcional: prepara la toolchain de Linux
uv run --locked scons install
```

En todos los sistemas, instala los requisitos y ejecuta `uv run --locked scons install`; `dotfiles.sh` es opcional en Linux. Este objetivo sincroniza todos los grupos de dependencias de Python, ejecuta `pnpm ci`, garantiza el binario nativo de Electron y descarga NATS y FFmpeg. `pnpm ci` recrea `frontend/node_modules` desde el lockfile. Chromium para la aplicación viene con Electron; Playwright se usa como cliente CDP y no necesita descargar navegadores.

Copia `.env.example` a `.env` si necesitas variables de entorno para desarrollo. La configuración personal de las instalaciones de escritorio se guarda en `~/.vectora/` (en Windows, `%USERPROFILE%\.vectora\`). Consulta [Configuración](./configuration) y la [referencia de variables de entorno](../../reference/config).

Inicia el backend y sirve la aplicación:

```bash
uv run vectora start
```

Para desarrollar el frontend con Vite, ejecuta esto en otra terminal:

```bash
pnpm --dir frontend dev
```

La primera cuenta creada en una instancia obtiene acceso de administración inicial. En un servidor expuesto a una red, configura el acceso y el registro según [Autenticación](../../security/authentication); no expongas el puerto directamente a Internet sin TLS y controles de acceso.

## Proveedores y credenciales

Configura al menos un proveedor compatible para el chat. Embeddings, reranking, búsqueda web, transcripción y modelos de decisión son capacidades separadas: configura solo el proveedor que necesites para cada función. Vectora no exige una clave de Cohere para iniciar el chat ni una clave de búsqueda para usar el workspace.

Las credenciales se pueden configurar en **Configuración → Modelos de IA** o **Provider Routing**, según el tipo de proveedor, o mediante variables de entorno. Lee [Configuración de modelos](../../guides/using-settings) y [BYOK y privacidad](../../security/byok-privacy) antes de enviar datos a un servicio externo.

## Licencia y servicios

El código está bajo [AGPL-3.0](https://github.com/vectora-company/vectora/blob/master/LICENSE). El uso local individual es gratuito; las funciones que dependen de servicios de pago pueden requerir una cuenta o una suscripción Pro activa. Esto no cambia los términos de licencia del código.

## Siguiente paso

→ [Inicio rápido](./quick-start)

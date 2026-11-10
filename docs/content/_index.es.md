---
title: Documentación de Vectora
type: docs
cascade:
  type: docs
sidebar:
  open: true
---

**Vectora** es un workspace de desarrollo asistido por IA. Reúne chat, archivos, terminal, Git, navegador, búsqueda en el proyecto y herramientas de automatización, manteniendo al usuario en control del workspace, los permisos y los proveedores conectados.

La aplicación Vectora es **open source bajo AGPL-3.0**. Consulta la [licencia](https://github.com/vectora-ltda/vectora/blob/master/LICENSE), el [repositorio](https://github.com/vectora-ltda/vectora), la [guía para contribuir](https://github.com/vectora-ltda/vectora/blob/master/CONTRIBUTING.md) y las [issues públicas](https://github.com/vectora-ltda/vectora/issues). El uso local individual es gratuito; una suscripción Pro habilita funciones y servicios de pago. Los proveedores externos pueden cobrar por su propio uso. La suscripción no modifica la licencia del código.

## Empieza aquí

| Quiero...                           | Guía                                                  |
| ----------------------------------- | ----------------------------------------------------- |
| Instalar la aplicación              | [Instalación](./getting-started/installation)         |
| Abrir el proyecto por primera vez   | [Inicio rápido](./getting-started/quick-start)        |
| Entender los workspaces y permisos  | [Primer workspace](./getting-started/first-workspace) |
| Configurar modelos y proveedores    | [Configuración](./guides/using-settings)              |
| Usar el chat y las herramientas     | [Usar el chat](./guides/using-the-chat)               |
| Consultar comandos                  | [Referencia de la CLI](./reference/cli)               |
| Compilar desde el código u hospedar | [Despliegue](./deployment/requirements)               |
| Ver las novedades de esta versión   | [Novedades de 0.1.23](./guides/release-0.1.23)        |

## Datos y modelos

Vectora puede ejecutarse localmente o en una máquina que administras. Los modelos locales pueden mantener la inferencia en tu entorno; al configurar un proveedor remoto, las solicitudes necesarias se envían a ese proveedor. Antes de conectar servicios, lee [BYOK y privacidad](./security/byok-privacy) y revisa qué datos recibe cada integración.

El modo predeterminado usa SQLite y LanceDB para datos e índices locales. La configuración `complete` conecta servicios externos de almacenamiento que administras. Consulta [RAG y Context Graph](./concepts/rag), [storage](./concepts/storage) y los [requisitos de despliegue](./deployment/requirements).

## Sobre esta documentación

Este sitio contiene guías públicas de instalación, configuración, uso, seguridad y referencia. Sigue la línea estable `0.1.x`; consulta el [changelog del repositorio](https://github.com/vectora-ltda/vectora/blob/master/CHANGELOG.md) para el historial completo. Las funciones pueden depender de la configuración, el sistema operativo, el proveedor o un servicio externo. Una prueba automatizada omitida (`skipped`) no demuestra disponibilidad operativa.

## Límites conocidos de la versión 0.1.23

- **Sandbox:** el aislamiento depende del sistema operativo, del worker disponible y de la configuración del workspace. En Windows se usa WSL2; si `bwrap` no está disponible, el fallback no aísla filesystem ni red. Lee [Sandbox](./concepts/sandbox) antes de confiar en la ejecución automatizada.
- **Proveedores externos:** las solicitudes a LLM, embeddings, rerankers y búsqueda pueden enviar datos al servicio seleccionado. Una instalación local no significa que un proveedor remoto procese los datos localmente.
- **Validación:** las pruebas unitarias, el replay de cassettes y las pruebas live omitidas cubren alcances distintos. Consulta [Verificación y Evaluation Lab](./guides/verification-lab) antes de interpretar resultados.

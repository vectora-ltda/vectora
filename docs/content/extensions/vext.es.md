---
title: Formato y seguridad VEXT
weight: 1
---

VEXT es el formato versionado de extensiones de Vectora. Un archivo `.vext` determinista contiene `vectora-extension.json`, payload de producción, integridad SHA-256, firma Ed25519 opcional y metadatos SBOM.

## Manifiesto

El manifiesto declara `id`, `publisher`, `name`, versión SemVer, `api_version`, `protocol_version`, runtime, entrypoints, capacidades y plataformas. El runtime puede ser `python`, `node` o `none`. Se rechazan traversal, enlaces simbólicos, duplicados y colisiones con metadatos.

## Compilar y verificar

```bash
vext build ./mi-extension --output ./dist/mi-extension.vext
vext validate ./dist/mi-extension.vext
vext inspect ./dist/mi-extension.vext
vext verify ./dist/mi-extension.vext
```

El builder excluye dependencias de desarrollo, aplica límites de paquete y contenido descomprimido y publica atómicamente. La verificación compara cada miembro regular del ZIP con el registro de integridad antes de cargar código.

## Confianza e instalación

Producción requiere una clave confiable del publisher. Desarrollo puede usar unsigned solo con opt-in explícito mediante `--allow-unsigned`. Las versiones son inmutables, la activación es atómica y el rollback usa la misma política de confianza y lock.

## Runtime y sandbox

Los adapters ejecutan fuera del proceso principal mediante JSON-RPC 2.0 delimitado por líneas. El host correlaciona IDs, limita mensajes, aplica timeout y valida respuestas. En Linux, Bubblewrap aísla la raíz escribible, elimina capacidades, separa namespaces y bloquea la red sin permiso. La falta de sandbox soportado falla de forma cerrada en producción.

## Bridge frontend

`@vectora/extension-sdk/sandbox` monta el bundle en un iframe con `sandbox="allow-scripts"`. La bridge valida el origen, correlaciona requests y rechaza llamadas pendientes en `dispose`.

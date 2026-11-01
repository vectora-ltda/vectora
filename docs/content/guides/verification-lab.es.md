---
title: Verificación y Evaluation Lab
weight: 9
---

Esta guía muestra cómo configurar los quality gates que verifican el resultado de una ejecución y cómo usar el Evaluation Lab para grabar, reproducir y comparar ejecuciones.

> **Estado:** estas capacidades forman parte de la línea 0.3. Esta página no declara concluidas las pruebas live que todavía aparecen como `skipped`.

## Requisitos previos

- Un workspace abierto en Vectora.
- Los comandos que usan los gates (por ejemplo `pytest`, `npm test` o un linter) instalados en el entorno del workspace.
- Para el Evaluation Lab: la CLI `vectora` disponible en la terminal.

## Quality gates

Después de una ejecución, Vectora ejecuta los gates del perfil de verificación del workspace. El perfil se guarda en `verification/<workspace_id>.json`, dentro del home de Vectora (`~/.vectora` por defecto, o el directorio definido en `VECTORA_HOME`).

Sin un perfil guardado, el workspace usa un perfil por defecto activo y sin gates: ninguna verificación bloquea la ejecución.

### Campos del perfil

| Campo | Descripción |
| --- | --- |
| `enabled` | Activa o desactiva la verificación automática |
| `gates` | Lista de gates (ver abajo) |
| `require_clean_git` | Exige un árbol de trabajo limpio después de la ejecución |
| `inject_stack_trace_on_failure` | Reinyecta la salida de error en el siguiente turno para que el agente la corrija |

Cada gate tiene:

| Campo | Descripción |
| --- | --- |
| `id`, `name` | Identificador y nombre visible |
| `command` | Comando y argumentos, como lista (no puede estar vacía) |
| `policy` | `block` (falla la ejecución), `degrade` (la marca como degradada) o `inform` (solo avisa); por defecto `block` |
| `file_patterns` | Patrones glob de los archivos que activan el gate |
| `timeout_seconds` | Tiempo máximo, de 5 a 3600 segundos; por defecto 300 |

Ejemplo:

```json
{
  "workspace_id": "mi-workspace",
  "enabled": true,
  "gates": [
    {
      "id": "pytest",
      "name": "Pruebas de Python",
      "command": ["pytest", "-q"],
      "policy": "block",
      "file_patterns": ["**/*.py"],
      "timeout_seconds": 600
    }
  ],
  "require_clean_git": false,
  "inject_stack_trace_on_failure": true
}
```

### Resultados

Cada verificación termina en uno de estos resultados: `passed`, `failed`, `infra_error`, `not_run`, `blocked`, `degraded` o `unverified`. Una prueba que no se ejecutó (`not_run`) es distinta de una prueba aprobada. Cada gate informa también el motivo: `passed`, `assertion_failed`, `timeout` o `dispatch_error`.

Si el archivo del perfil está dañado, Vectora aplica un gate de protección que falla con la política `block`, en lugar de omitir la verificación.

## Evaluation Lab

El Evaluation Lab graba cassettes de las ejecuciones con los datos sensibles redactados y permite reproducirlas sin llamar al proveedor externo.

```bash
vectora eval run --suite ruta/a/suite.json
vectora eval compare baseline.json candidata.json
```

- `vectora eval run` ejecuta un benchmark o una suite de cassettes.
- `vectora eval compare` compara dos cassettes (baseline y candidata).
- Los dos comandos aceptan `--output human` (por defecto) o `--output json`.

## Limitaciones

- El replay reproduce un cassette grabado; no demuestra que el proveedor, el Browser Workbench u otra integración estén disponibles en vivo.
- Las validaciones live marcadas como `skipped` no cuentan como aprobación.
- Los gates se ejecutan en el entorno del workspace; un comando ausente (`dispatch_error`) o un timeout producen `infra_error`, no un fallo de prueba.

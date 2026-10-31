---
title: Using the Chat
weight: 1
---

## Enviando mensajes

El input del chat acepta texto, archivos arrastrados, imágenes pegadas del portapapeles y `@menciones` de archivos para traer contenido específico al contexto sin tener que describirlo.

## Selector de modelo

Solo muestra proveedores con una API key configurada — Google Gemini, OpenAI, Anthropic, Cohere, u Ollama local. Cambiar de modelo a mitad de conversación no pierde el historial.

## Modos de permiso

Controlan cuándo el agente se detiene para pedir aprobación humana:

| Modo                  | Comportamiento del gate de aprobación                                                               |
| --------------------- | ---------------------------------------------------------------------------------------------------- |
| **Siempre preguntar** | se pausa para herramientas clasificadas como sensibles o destructivas                              |
| **Aceptar ediciones** | permite escribir y editar archivos sin pausa; otras acciones protegidas siguen sujetas a HITL     |
| **Autónomo**          | no se pausa para la aprobación humana habitual; la policy y los bloqueos de seguridad siguen activos |
| **Plan**              | se pausa para la primera acción protegida del turno; cada llamada de medios con coste requiere su propia aprobación |
| **Bypass**            | desactiva las pausas habituales de HITL; los guardrails independientes siguen aplicándose          |

Son modos de permiso, no niveles de confianza en el modelo. Algunas operaciones, como el control del escritorio, pueden seguir exigiendo confirmación en cualquier modo. En **Plan**, una aprobación concedida en el mismo turno permite continuar; no se convierte en una autorización permanente.

Una preaprobación es una concesión explícita e independiente. Se puede consumir una sola vez para una herramienta y sus argumentos exactos, dentro del mismo workspace y `run_id`, y caduca tras un plazo limitado. Las herramientas irreversibles no aceptan esta concesión.

En modo **Autónomo**, un Decision Model participa solo si está habilitado, seleccionado, disponible y permitido para la superficie de la acción. Sin él, no se presume una decisión automatizada adicional: la policy estática sigue controlando lo que puede ejecutarse. Si una evaluación iniciada falla, vence el plazo o exige revisión humana, la acción no se considera aprobada.

Consulta [Orchestrator & Subagents](../../concepts/sub-agents) para el flujo HITL y [Modelos de decisión](../decision-models) para configurar providers y superficies.

## Pensamiento del orquestador

El bloque de "pensamiento" muestra la decisión del orquestador antes de actuar: responder directamente o delegar a `coder`/`search`, y por qué. Esto es transparencia real, no solo un spinner — puedes ver el razonamiento, no solo esperar el resultado.

## Memoria entre sesiones

Aparece una insignia "🧠 N memorias cargadas" cuando el agente usa memorias persistentes de conversaciones anteriores en esa respuesta. Gestiona las memorias manualmente en **Configuración → Preferencias → Memoria**.

## Citas de RAG

Las respuestas basadas en contenido indexado traen citas clicables `[1] [2]` — haz clic para ver el extracto original y la fuente.

## Multiusuario (Pro)

En modo Pro con chat web multiusuario, los hilos pueden compartirse entre miembros del mismo workspace, con RBAC controlando quién ve qué.

## Ver también

- [Usando el workbench](../using-the-workbench) — el panel lateral que acompaña al chat
- [Usando la configuración](../using-settings) — dónde viven el modelo por defecto, el idioma y el tema

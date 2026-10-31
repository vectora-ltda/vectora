---
title: Modelos de decisión
weight: 2
---

Los Decision Models evalúan decisiones estructuradas del runtime, como el riesgo de una llamada a una herramienta. No sustituyen los permisos de las herramientas, la policy del workspace ni las etapas de aprobación.

## Configuración

En **Configuración → Modelos de IA**, selecciona un provider disponible, configura las credenciales necesarias y habilita Decision Models. La matriz de superficies controla dónde se puede consultar el modelo, por ejemplo HITL, subagentes, Plan Mode, Browser, MCP, terminal, red y tareas en segundo plano.

Las preferencias se asocian al usuario y al workspace activo. Al cambiar de workspace, vuelve a comprobar el provider y las superficies habilitadas. Una superficie desactivada no debe quedar autorizada por la preferencia de otra superficie.

## Cómo afecta una decisión a una acción

1. El runtime comprueba la configuración y la superficie aplicable.
2. Si la evaluación está habilitada y el provider está listo, el modelo devuelve una decisión estructurada.
3. La policy y los controles locales siguen teniendo autoridad. El modelo puede exigir más aprobación o bloquear una acción; su respuesta no concede permisos que la policy haya denegado.
4. Si una evaluación ya iniciada supera el plazo, falla, devuelve contenido inválido o requiere revisión humana, la acción no se considera aprobada.
5. Si Decision Model está deshabilitado o no está disponible antes de iniciar la evaluación, no se presume una decisión automatizada adicional; los gates estáticos del runtime siguen vigentes.

## Límites

- Habilitar la función no hace que todos los providers estén disponibles. Las credenciales, el estado de salud, las dependencias locales y la conectividad deben funcionar en el entorno actual.
- Una opción seleccionada en la interfaz expresa una preferencia. No demuestra que el provider haya respondido ni que se haya ejecutado una evaluación.
- Las pruebas unitarias y de integración no demuestran la disponibilidad live de Clef, Laya o JEV. Los resultados `skipped` deben seguir identificados como no ejecutados.
- Decision Models no sustituyen la confirmación humana cuando la policy o el resultado exigen aprobación.

## Seguridad y datos

Las decisiones se asocian al contexto de ejecución. No incluyas credenciales ni contenido privado en los campos de decisión. La interfaz debe ocultar los secretos y los registros de auditoría deben guardar solo los metadatos y resúmenes necesarios para explicar la decisión.

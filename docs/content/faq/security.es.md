---
title: Seguridad
weight: 3
---

**¿Mis datos salen de mi servidor?**
Depende de las funciones que uses. Los proveedores externos reciben los datos necesarios para las solicitudes que envías; las integraciones remotas reciben los datos necesarios para sus operaciones. Vectora también puede usar servicios auxiliares para autenticación, licencias, actualizaciones o funciones habilitadas. Consulta [BYOK y privacidad](../../security/byok-privacy).

**¿Vectora tiene cifrado de extremo a extremo?**
No. El backend necesita leer el contenido para ejecutar herramientas, indexar el workspace y enviar contexto al modelo seleccionado. Protege la máquina y las copias de seguridad que almacenan tus datos.

**¿Cómo gestiona Vectora la inyección de prompts?**
El contenido de archivos, páginas y resultados de herramientas se trata como entrada no confiable. El agente también aplica políticas y aprobaciones HITL a las operaciones según el modo de permisos y la configuración del workspace. Estos controles reducen los riesgos, pero no hacen seguro ejecutar instrucciones externas sin revisión.

**¿Puedo restringir lo que puede hacer el agente?**
Sí. Configura el modo HITL, las políticas de herramientas, la confianza del workspace y los controles del sandbox. La disponibilidad de cada protección depende de la plataforma y la configuración. Consulta las guías de [seguridad](../../security/) y [workspaces](../../getting-started/first-workspace).

**¿Dónde informo de una vulnerabilidad?**
Sigue la [política de seguridad del repositorio](https://github.com/vectora-company/vectora/blob/master/SECURITY.md) en lugar de publicar detalles en una issue.

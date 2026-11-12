---
title: BYOK y privacidad
weight: 4
---

## Qué significa self-hosted

El backend y los datos locales de Vectora permanecen en la máquina o infraestructura que operas. Esto no significa que todo el procesamiento sea offline ni que ningún dato salga de esa infraestructura. Cuando usas un modelo o integración externa, Vectora envía a ese servicio los datos necesarios para la operación solicitada — por ejemplo, tu mensaje y el contexto seleccionado para una llamada al LLM.

Vectora también puede comunicarse con servicios auxiliares para funciones como autenticación, licencias, actualizaciones e integraciones que habilites. Revisa la configuración de la instancia, los endpoints activos y las políticas de los servicios que elijas. No envíes datos confidenciales a un proveedor externo sin autorización para hacerlo.

## BYOK (Bring Your Own Key)

Configuras los proveedores y las credenciales que deseas usar. El backend local almacena las claves según el mecanismo de secretos de la instalación; esto no impide que el proveedor reciba una solicitud cuando lo seleccionas. Los términos del proveedor rigen los costes, la retención, el entrenamiento y el uso de datos. Los modelos locales pueden reducir el envío a terceros, pero funciones que dependen de la red, como búsqueda web o integraciones MCP remotas, siguen transmitiendo los datos que necesitan.

## Protección de los datos almacenados

Vectora guarda sesiones, ajustes e índices en la infraestructura que administras. Quien opera esa máquina puede acceder a los datos locales. Protege el sistema operativo, las copias de seguridad, los volúmenes y las credenciales; self-hosting no es cifrado de extremo a extremo ni protege los datos del administrador del servidor.

## GDPR y protección de datos

Quien opera la instancia debe evaluar la base legal y las políticas de cada proveedor o integración antes de enviar datos personales. Vectora no sustituye esa evaluación ni las obligaciones del operador.

## Informar de una vulnerabilidad

Usa el canal indicado en la [política de seguridad](https://github.com/vectora-company/vectora/blob/master/SECURITY.md); no publiques detalles explotables en una issue pública.

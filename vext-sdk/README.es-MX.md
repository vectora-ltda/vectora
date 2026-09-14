# SDK VEXT de Vectora

El SDK VEXT proporciona contratos TypeScript y un puente de sandbox para extensiones. Se desarrolla como un monorepo interno y publica `@vext/sdk` y los paquetes compartidos `@vext/*`.

[English](README.md) · [Português](README.pt-BR.md)

## Instalación

```bash
npm install @vext/sdk
```

## Exportaciones

La exportación principal contiene tipos de manifiesto, capacidades, JSON-RPC y contexto. La exportación `@vext/sdk/sandbox` contiene `mountSandboxedExtension`, que carga un bundle frontend en un iframe con `sandbox="allow-scripts"`.

## Desarrollo

Ejecuta `npm run check` en este directorio para comprobar tipos y generar el paquete. El protocolo está documentado en la [documentación VEXT](../../docs/content/extensions/vext.es.md).

## Soporte y licencia

Lee [CONTRIBUTING.es-MX.md](CONTRIBUTING.es-MX.md) antes de enviar cambios. Los reportes de seguridad están en [SECURITY.es-MX.md](SECURITY.es-MX.md). El proyecto usa Apache License 2.0; consulta [LICENSE](LICENSE).

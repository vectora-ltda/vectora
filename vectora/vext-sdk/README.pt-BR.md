# SDK VEXT do Vectora

O SDK VEXT fornece contratos TypeScript e uma ponte de sandbox para extensões. Ele é desenvolvido como um monorepo interno e publica `@vext/sdk` e os pacotes compartilhados `@vext/*`.

[English](README.md) · [Español](README.es-MX.md)

## Instalação

```bash
npm install @vext/sdk
```

## Exportações

O export principal contém tipos de manifesto, capabilities, JSON-RPC e contexto. O export `@vectora/extension-sdk/sandbox` contém `mountSandboxedExtension`, que carrega um bundle frontend em iframe com `sandbox="allow-scripts"`.

## Desenvolvimento

Execute `npm run check` neste diretório para verificar tipos e gerar o pacote. O protocolo está documentado na [documentação VEXT](../../docs/content/extensions/vext.pt.md).

## Suporte e licença

Leia [CONTRIBUTING.pt-BR.md](CONTRIBUTING.pt-BR.md) antes de enviar alterações. Relatos de segurança estão em [SECURITY.pt-BR.md](SECURITY.pt-BR.md). O projeto usa a Apache License 2.0; consulte [LICENSE](LICENSE).

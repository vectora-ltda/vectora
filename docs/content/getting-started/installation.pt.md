---
title: Instalação
weight: 2
---

Há duas formas de usar o Vectora: instalar um pacote desktop publicado ou executar o projeto a partir do código-fonte. Os pacotes incluem o backend e o frontend; não é necessário instalar Python ou Node para usar o aplicativo desktop.

## Instalar a versão publicada

Abra a página de [releases do Vectora no GitHub](https://github.com/vectora-company/vectora/releases) e escolha um artefato compatível com o seu sistema e arquitetura. A matriz publicada atualmente cobre:

| Sistema | Arquitetura publicada | Formatos disponíveis |
| --- | --- | --- |
| Windows | x64 e ARM64 | `.exe`, `.msi` |
| macOS | Apple Silicon (ARM64) | `.dmg` |
| Linux | x64 e ARM64 | `.AppImage`, `.deb`, `.rpm` |

Os artefatos são gerados por CI; consulte a página da release para saber quais arquivos foram efetivamente publicados. A disponibilidade de cada formato não garante que todas as distribuições Linux tenham as mesmas dependências de sistema. No Linux, leia as dependências declaradas pelo pacote e os requisitos da sua distribuição antes de instalar.

No Windows, execute o instalador e siga as etapas na tela. No macOS, abra o DMG e arraste o aplicativo para Applications. No Linux, use o gerenciador de pacotes para `.deb`/`.rpm`; para AppImage, conceda permissão de execução antes de abrir. A assinatura e a notarização variam por artefato e devem ser verificadas nas informações da release.

## Executar a partir do código-fonte

Use este caminho para desenvolvimento ou para inspecionar/modificar o projeto. Consulte também o [guia de contribuição](https://github.com/vectora-company/vectora/blob/master/CONTRIBUTING.md).

**Pré-requisitos:** [uv](https://docs.astral.sh/uv/), Python 3.13, Node.js 26 e pnpm 11.5. No Linux, o script `dotfiles.sh` identifica a distribuição e prepara a toolchain e as dependências nativas; ele para em Windows/macOS.

```bash
git clone https://github.com/vectora-company/vectora.git
cd vectora

# ./dotfiles.sh  # opcional: prepara a toolchain Linux
uv run --locked scons install
```

Em todos os sistemas, instale os pré-requisitos e rode `uv run --locked scons install`; o `dotfiles.sh` é opcional no Linux. Esse alvo sincroniza todos os grupos Python, executa `pnpm ci`, garante o binário nativo do Electron e baixa NATS e FFmpeg. `pnpm ci` recria `frontend/node_modules` a partir do lockfile. O Chromium de execução é o que acompanha o Electron; Playwright é usado como cliente CDP e não exige download de browsers.

Copie `.env.example` para `.env` se quiser configurar variáveis para o ambiente de desenvolvimento. Para uma instalação desktop, as configurações pessoais ficam em `~/.vectora/` (no Windows, em `%USERPROFILE%\.vectora\`). Veja [Configuração](./configuration) e a [referência de variáveis](../../reference/config).

Para iniciar o backend e servir a aplicação:

```bash
uv run vectora start
```

Para desenvolver o frontend com Vite em outro terminal:

```bash
pnpm --dir frontend dev
```

A primeira conta criada em uma instância habilita a administração inicial. Em um servidor exposto, configure o acesso e o signup de acordo com [Autenticação](../../security/authentication); não publique a porta diretamente na internet sem TLS e controles de acesso.

## Providers e credenciais

É necessário configurar ao menos um provider de chat compatível. Embeddings, reranking, busca web, transcrição e modelos de decisão são capacidades separadas: configure somente o provider necessário para o recurso que deseja usar. O Vectora não exige uma chave Cohere para iniciar o chat nem uma chave de busca para usar o workspace.

As chaves podem ser configuradas pela interface em **Configurações → Modelos de IA** ou **Provider Routing**, conforme o tipo de provider, ou por variáveis de ambiente. Leia [Configuração de modelos](../../guides/using-settings) e [BYOK e privacidade](../../security/byok-privacy) antes de enviar dados a um serviço externo.

## Licença e serviços

O código está sob [AGPL-3.0](https://github.com/vectora-company/vectora/blob/master/LICENSE). O uso local individual é gratuito; recursos que dependem dos serviços pagos podem exigir uma conta ou uma assinatura Pro ativa. Isso não altera os termos da licença do código.

## Próximo passo

→ [Início rápido](./quick-start)

---
title: Formato e segurança VEXT
weight: 1
---

VEXT é o formato versionado de extensões do Vectora. Um arquivo `.vext` determinístico contém `vectora-extension.json`, payload de produção, integridade SHA-256, assinatura Ed25519 opcional e metadados SBOM.

## Manifesto

O manifesto declara `id`, `publisher`, `name`, versão SemVer, `api_version`, `protocol_version`, runtime, entrypoints, capabilities e plataformas. O runtime pode ser `python`, `node` ou `none`. Caminhos são relativos em formato POSIX; traversal, links simbólicos, duplicatas e colisões com metadados são rejeitados.

## Build e verificação

```bash
vext build ./minha-extensao --output ./dist/minha-extensao.vext
vext validate ./dist/minha-extensao.vext
vext inspect ./dist/minha-extensao.vext
vext verify ./dist/minha-extensao.vext
```

O builder exclui dependências de desenvolvimento, aplica limites de pacote e conteúdo descompactado e publica atomicamente. A verificação compara cada membro regular do ZIP com o registro de integridade antes de carregar código.

## Confiança e instalação

Produção exige uma chave confiável do publisher. Desenvolvimento pode usar unsigned somente com opt-in explícito:

```bash
vext install ./dist/minha-extensao.vext --root ~/.vectora/extensions --allow-unsigned
vext install ./dist/minha-extensao.vext --root ~/.vectora/extensions --public-key publisher.pub
```

Versões são imutáveis e a ativação é atômica. Rollback usa a mesma política de confiança e lock. Chaves revogadas não podem ser instaladas, reativadas ou iniciadas.

## Runtime e sandbox

Adapters executam fora do processo principal via JSON-RPC 2.0 delimitado por linhas. O host correlaciona IDs, limita mensagens, aplica timeout e valida respostas `result`/`error`. Em produção Linux, Bubblewrap isola a raiz gravável, remove capabilities, separa namespaces e bloqueia rede sem permissão. Ausência de sandbox suportado falha fechado.

## Bridge frontend

`@vectora/extension-sdk/sandbox` monta o bundle em iframe com `sandbox="allow-scripts"`. A bridge valida a origem do iframe, correlaciona requests e rejeita chamadas pendentes em `dispose`. O host continua responsável por validar método, capability e payload.

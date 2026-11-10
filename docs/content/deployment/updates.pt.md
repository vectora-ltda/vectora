---
title: Atualizações
weight: 5
---

## Aplicativo desktop

O Vectora Desktop verifica atualizações pelo canal configurado para a instalação. Quando uma versão está disponível, o aplicativo mostra seu estado e as notas de release, prepara um backup rotativo dos dados locais e baixa o pacote sem instalá-lo imediatamente. A instalação é aplicada ao sair do aplicativo. O usuário pode controlar a verificação automática nas preferências e iniciar uma verificação manual.

O comportamento pode depender do sistema operacional, do formato instalado e do canal publicado. Consulte as [releases do GitHub](https://github.com/vectora-ltda/vectora/releases) para conferir versões e artefatos disponíveis.

## Build do código-fonte

Uma execução iniciada com `uv run vectora start` não usa o atualizador do Electron. Atualize o checkout e as dependências pelo fluxo de desenvolvimento documentado em [Instalação](../../getting-started/installation).

---
title: Segurança
weight: 3
---

**Meus dados saem do meu servidor?**
Depende dos recursos usados. Providers externos recebem os dados necessários às solicitações que você envia; integrações remotas recebem os dados necessários às suas operações. O Vectora também pode usar serviços auxiliares para autenticação, licenciamento, atualizações ou funções habilitadas. Veja [BYOK e privacidade](../../security/byok-privacy).

**O Vectora tem criptografia ponta a ponta?**
Não. O backend precisa ler o conteúdo para executar ferramentas, indexar o workspace e enviar contexto ao modelo escolhido. Proteja a máquina e os backups que armazenam os dados.

**Como o Vectora lida com prompt injection?**
O conteúdo de arquivos, páginas e resultados de ferramentas é tratado como entrada não confiável. O agente também aplica políticas e aprovações HITL às operações conforme o modo de permissão e a configuração do workspace. Esses controles reduzem riscos, mas não tornam seguro executar instruções externas sem revisão.

**Posso restringir o que o agente pode fazer?**
Sim. Configure o modo HITL, políticas de ferramentas, confiança do workspace e controles do sandbox. A disponibilidade de cada proteção depende da plataforma e da configuração. Consulte os guias de [segurança](../../security/) e [workspaces](../../getting-started/first-workspace).

**Onde reporto uma vulnerabilidade?**
Siga a [política de segurança do repositório](https://github.com/vectora-company/vectora/blob/master/SECURITY.md), em vez de publicar detalhes em uma issue.

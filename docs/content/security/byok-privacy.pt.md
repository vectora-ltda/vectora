---
title: BYOK e privacidade
weight: 4
---

## O que significa self-hosted

O backend e os dados locais do Vectora ficam na máquina ou infraestrutura que você opera. Isso não significa que todo processamento ocorre offline ou que nenhum dado sai dessa infraestrutura. Quando você usa um modelo ou integração externa, o Vectora envia a esse serviço os dados necessários à operação solicitada — por exemplo, a mensagem e o contexto selecionado para uma chamada de LLM.

O Vectora também pode se comunicar com serviços auxiliares para funções como autenticação, licenciamento, atualizações e integrações que você habilitar. Consulte as configurações da instância, os endpoints ativos e as políticas dos serviços que escolher. Não inclua dados confidenciais em um provider externo sem autorização para fazê-lo.

## BYOK (Bring Your Own Key)

Você configura os providers e as credenciais que deseja usar. As chaves são armazenadas pelo backend local conforme o mecanismo de secrets da instalação; isso não impede que o provider receba a solicitação quando você o seleciona. Custos, retenção, treinamento e uso dos dados são regidos pelos termos do provider. Modelos locais podem reduzir o envio a terceiros, mas dependências de rede — como busca web ou integrações MCP remotas — continuam transmitindo os dados necessários a elas.

## Proteção dos dados armazenados

O Vectora mantém sessões, configurações e índices na infraestrutura que você administra. O operador dessa máquina pode acessar os dados locais. Proteja o sistema operacional, backups, volumes e credenciais; self-hosting não é criptografia ponta a ponta e não protege contra o administrador do próprio servidor.

## LGPD e GDPR

Quem opera a instância deve avaliar a base legal e as políticas de cada provider ou integração antes de enviar dados pessoais. O Vectora não substitui essa avaliação nem as obrigações do operador.

## Relatar uma vulnerabilidade

Use o canal descrito na [política de segurança](https://github.com/vectora-company/vectora/blob/master/SECURITY.md); não publique detalhes exploráveis em uma issue pública.

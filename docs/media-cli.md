# Ferramentas de mídia via CLI

`vectora media list` lista as quatro tools nativas. `vectora media image
<prompt>`, `speech <text>`, `video <prompt>` e `analyze-video <path> <question>`
usam o mesmo `ToolSpec` registrado pelo agente, com validação de argumentos e
política de tools para o principal local. Todos aceitam `--output json`,
`--model` e `--thread-id`.

A CLI não recebe chaves de provider e não imprime segredos. `analyze-video`
deve receber um artefato autorizado pela sessão; a tool nativa mantém as
validações de acesso e provider. O servidor MCP usa transporte stdio e exige
`VECTORA_MCP_USER_ID` entregue por um launcher local confiável; essa variável
não é autenticação de transporte para processos arbitrários.

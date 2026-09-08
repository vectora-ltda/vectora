# Ferramentas de mídia via CLI

`vectora media list` lista as quatro tools nativas. `vectora media image
<prompt>`, `speech <text>`, `video <prompt>` e `analyze-video <path> <question>`
usam o mesmo `ToolSpec` registrado pelo agente, com validação de argumentos e
política de tools por usuário. Todos aceitam `--output json`, `--model`,
`--user-id` e `--thread-id`.

A CLI não recebe chaves de provider e não imprime segredos. `analyze-video`
deve receber um artefato autorizado pela sessão; a tool nativa mantém as
validações de acesso e provider. O servidor MCP externo fica condicionado à
definição do transporte e credencial de autenticação da issue.

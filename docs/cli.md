# CLI de MCP e skills

A CLI usa os mesmos serviços e stores dos endpoints REST. Isso mantém o estado
do agente consistente entre o aplicativo, a API e os comandos de terminal.

## Comandos disponíveis

Use `vectora mcp list`, `search <texto>`, `info <id>`, `install <id>` e
`remove <id>` para servidores MCP. Use `vectora skills list`, `search <texto>`,
`info <id>`, `install <url-ou-path>`, `remove <id>` e `validate <id>` para
skills instaladas. `vectora skills publish <source> <name> <description>` exige
uma sessão autenticada da conta Vectora.

Todos os comandos aceitam `--output json`. A resposta contém
`schema_version`, `status`, `data` e `error` quando aplicável. O código de saída
é `0` para sucesso, `1` para falha operacional e `2` para erro de uso ou
validação de argumentos.

Os comandos `enable`, `disable`, `update` e `ext` ainda estão bloqueados pelas
issues de scopes, versionamento e SDK `.vext`; não alteram estado nesta versão.

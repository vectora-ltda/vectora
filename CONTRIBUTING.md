# Contribuindo com o Vectora

Obrigado pelo interesse. Este arquivo explica como preparar o ambiente, escolher uma tarefa e abrir um PR. A documentação para quem usa o produto fica em `docs/` (site público); a documentação de desenvolvimento fica aqui e nas issues do repositório.

## Antes de começar

- Leia o [código de conduta](CODE_OF_CONDUCT.md).
- Para relatar uma vulnerabilidade, **não abra issue**: siga o [SECURITY.md](SECURITY.md).
- O Vectora é licenciado sob a AGPL-3.0 ([LICENSE](LICENSE)). Para contribuir você precisa aceitar o [CLA](CLA.md), que permite à Vectora Ltda distribuir o projeto também sob outras licenças. O aceite é um comentário no primeiro PR: `Li o CLA.md e concordo com os seus termos.`

## Ambiente

Requisitos: [uv](https://docs.astral.sh/uv/) (instala o Python 3.13), Node.js 24 ou superior e [pnpm](https://pnpm.io/).

```bash
git clone https://github.com/vectora-ltda/vectora.git
cd vectora
uv sync
pnpm --dir frontend install
cp .env.example .env
```

Para subir o backend em desenvolvimento: `uv run vectora start`.

## Checks

O hook de pre-commit é a fonte de verdade e roda os mesmos checks do CI.

```bash
uv run pre-commit install
uv run pre-commit run --all-files
uv run ruff check backend tests
uv run ty check backend tests
uv run pytest tests/unit
pnpm --dir frontend run typecheck
pnpm --dir frontend exec oxlint
```

`scons lint` e `scons tests` rodam o conjunto completo a partir da raiz. Rode os checks da área que você mexeu e diga no PR o que rodou e o que não rodou.

## Regras do código

- **Testes primeiro:** um bug começa por um teste que o reproduz; uma funcionalidade nova traz pelo menos um teste do caminho feliz e um do caminho de erro.
- **Tipos:** Python com anotações completas e `ty` limpo; TypeScript estrito e `tsc --noEmit` limpo.
- **Texto da interface:** nunca fixo no código. Passe por `m()` de `@/lib/paraglide/messages` e adicione a chave nos três idiomas (`messages/en.json`, `es.json`, `pt.json`); depois rode `pnpm --dir frontend run i18n:compile`.
- **I/O assíncrono:** rede, banco, arquivos e subprocessos usam `async`/`await`. Nada de `requests` nem `subprocess.run` no backend.
- **Ferramentas do agente** capturam exceções e devolvem um erro tipado ao modelo, com log estruturado.
- **Conteúdo lido por ferramentas** (arquivos, páginas, resultados) é dado não confiável e nunca tem autoridade de instrução.
- **Comentários** descrevem o que o código faz e as invariantes que ele preserva; não registram histórico, planejamento nem tarefas pendentes.
- Planejamento fica em issues e em markdown; stubs, `TODO` e mocks não entram no código de produção.

## Fluxo de trabalho

1. Escolha uma issue. As marcadas com `good first issue` e `help wanted` são as indicadas para quem está chegando; comente na issue antes de começar.
2. Crie uma branch a partir da base certa (veja abaixo).
3. Faça mudanças pequenas e focadas; cada commit usa Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `ci:`, `chore:`).
4. Abra o PR com título no mesmo formato, em português, e descreva o que muda e como você verificou. O título e a descrição não podem citar ferramentas de IA, planos de trabalho nem números de issue ou PR; o CI recusa.
5. Atenda à revisão. Um PR só é mesclado com os checks obrigatórios verdes e o aceite do CLA.

## Linhas de release

A configuração das linhas está em `.github/release-lines.json`.

| Linha                      | Branch base   | Milestone |
| -------------------------- | ------------- | --------- |
| Manutenção da série atual  | `master`      | `0.3.x`   |
| Desenvolvimento da próxima | `release/0.4` | `0.4`     |

Todo PR usa a base da linha que ele atinge e a milestone correspondente; o workflow `PR Release Milestone` atribui a milestone e bloqueia combinações incompatíveis. As versões são calculadas dos commits pelo workflow de release, que abre o PR `chore(master): release X.Y.Z`. Uma correção aplicada na linha de manutenção também precisa chegar à linha de desenvolvimento; o workflow de propagação abre esse PR.

## O que o CI executa em PRs

Os workflows de PR rodam a versão do próprio PR. Eles validam título (Conventional Commits), ausência de referências proibidas no texto do PR, milestone, labels, lint, tipos, testes, varredura de segurança (Snyk) e o build dos instaladores. PRs de fork rodam sem segredos: rotulagem, milestone e comentários automáticos só valem para branches do próprio repositório.

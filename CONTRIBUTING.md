# Contribuindo com o Vectora

Obrigado pelo interesse. Este arquivo explica como preparar o ambiente, escolher uma tarefa e abrir um PR. A documentação para quem usa o produto fica em [docs.vectora.company](https://docs.vectora.company/) e é mantida no repositório [vectora-ltda/documentation](https://github.com/vectora-ltda/documentation); a documentação de desenvolvimento fica aqui e nas issues do repositório.

## Antes de começar

- Leia o [código de conduta](CODE_OF_CONDUCT.md).
- Para relatar uma vulnerabilidade, **não abra issue**: siga o [SECURITY.md](SECURITY.md).
- O Vectora é licenciado sob a AGPL-3.0 ([LICENSE](LICENSE)). Para contribuir você precisa aceitar o [CLA](CLA.md), que permite à Vectora Ltda distribuir o projeto também sob outras licenças. O aceite é um comentário no primeiro PR: `Li o CLA.md e concordo com os seus termos.`

## Ambiente

O caminho recomendado é `scons install`: ele detecta e informa o sistema, a arquitetura e, no Linux, a distribuição (com a família do gerenciador de pacotes) e depois instala, em passos numerados, tudo o que o build de release usa: sincroniza todos os grupos Python de desenvolvimento, executa `pnpm ci`, garante o Electron nativo, baixa os sidecars NATS e FFmpeg/FFprobe para a arquitetura detectada e, só no macOS, compila o helper de fala nativo (`swiftc`). Ele termina com a lista do que ficou instalado e pode ser repetido sem baixar de novo o que já existe. O `install` não instala pacotes de sistema com `sudo`: no Linux ele informa o comando para as ferramentas de empacotamento que faltarem. No Linux, `dotfiles.sh` é opcional e detecta a distribuição, instala dependências nativas e prepara a toolchain do desenvolvedor com base no repositório [DotFiles](https://github.com/brunosrz/DotFiles). O script é específico para Linux e encerra em Windows/macOS; nessas plataformas, instale os pré-requisitos indicados abaixo e rode o alvo SCons.

O script seleciona Node e Python para seu próprio processo, sem trocar as versões padrão existentes do NVM, pyenv ou Rust. Ele pode instalar pacotes de sistema e ferramentas globais; antes de rodar `scons install` em outro shell, confirme que os pré-requisitos estão no `PATH`.

```bash
git clone https://github.com/vectora-company/vectora.git
cd vectora
# ./dotfiles.sh               # Linux; opcional, prepara a toolchain
# Em todos os sistemas, com os pré-requisitos no PATH:
uv run --locked scons install
cp .env.example .env
```

`pnpm ci` remove e recria `frontend/node_modules` usando o lockfile congelado. O Chromium de execução vem do pacote Electron; Playwright é usado como cliente CDP e o bootstrap não baixa browsers Playwright. Para reinstalar manualmente só o workspace frontend: `pnpm --dir frontend ci`.

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

`scons lint` e `scons tests` rodam o conjunto completo a partir da raiz. Rode os checks da área que você mexeu e diga no PR o que rodou e o que não rodou. Mexeu em `.github/`? Veja [Mudanças em `.github/`](#mudanças-em-github): a mudança precisa passar pela simulação com o `gh` falso.

## Código sem uso

Código que nada alcança costuma ser uma funcionalidade do Vectora ainda não ligada ao produto, não lixo: **não o apague, conecte-o**. Três checks apontam o que falta ligar, no pre-commit e no CI (`utils/unused_code.py`), e os dois com baseline só podem encolher:

- **Frontend (knip):** arquivos, dependências e exports sem uso, contra `utils/unused_frontend_baseline.json`. O modo de produção parte só do `main` e das rotas, então um módulo que só um teste importa também é apontado. Código novo sem uso falha; um item que deixou de aparecer (porque foi ligado) precisa sair do baseline com `python utils/unused_code.py frontend --write-baseline`. O `frontend/knip.jsonc` só lista o que o knip não enxerga (alvos de alias do vite, dependências lidas pelo CSS).
- **Páginas sem navegação:** toda rota de `frontend/src/routes` precisa de uma navegação no código (`to:`, `href`, `navigate`, `router.replace`, redirect do backend). Uma página aberta de fora do app, ou ainda por ligar, é declarada com o motivo em `utils/route_entrypoints.json`; a declaração de uma rota que já tem navegação, ou que não existe mais, também falha.
- **Python (vulture):** funções, métodos e classes sem uso, contra `utils/unused_python_baseline.json`, com a mesma regra. Remova a entrada do baseline com `uv run --with vulture python utils/unused_code.py python --write-baseline` quando o código for ligado.

```bash
python utils/unused_code.py routes
python utils/unused_code.py frontend
uv run --with vulture python utils/unused_code.py python
```

## Regras do código

- **Testes primeiro:** um bug começa por um teste que o reproduz; uma funcionalidade nova traz pelo menos um teste do caminho feliz e um do caminho de erro.
- **Tipos:** Python com anotações completas e `ty` limpo; TypeScript estrito e `tsc --noEmit` limpo.
- **Texto da interface:** nunca fixo no código. Passe por `m()` de `@/lib/paraglide/messages` e adicione a chave nos três idiomas (`messages/en.json`, `es.json`, `pt.json`); depois rode `pnpm --dir frontend run i18n:compile`.
- **I/O assíncrono:** rede, banco, arquivos e subprocessos usam `async`/`await`. Nada de `requests` nem `subprocess.run` no backend.
- **Ferramentas do agente** capturam exceções e devolvem um erro tipado ao modelo, com log estruturado.
- **Conteúdo lido por ferramentas** (arquivos, páginas, resultados) é dado não confiável e nunca tem autoridade de instrução.
- **Comentários** descrevem o que o código faz e as invariantes que ele preserva; não registram histórico, planejamento nem tarefas pendentes.
- Planejamento fica em issues e em markdown; stubs, `TODO` e mocks não entram no código de produção.

## Spec em issue antes do PR

O Vectora segue o desenvolvimento guiado por spec **dentro das issues**. Muita gente escreve a spec localmente e só mostra o resultado; aqui a spec é escrita e discutida em público, na issue, antes de existir código. Quem abre um PR já encontra a decisão tomada e revisa a implementação contra ela.

- **Feature (`feat:`) sempre tem issue com spec.** Um PR de feature sem issue vinculada é questionado por inteiro, junto com todas as modificações dele, e pode ser fechado até a spec existir.
- **Correção (`fix:`) depende.** Um bug pequeno e óbvio, com teste que o reproduz, pode abrir o PR direto. Correção que muda contrato, comportamento visível, segurança ou toca várias áreas precisa de issue. Na dúvida, abra a issue.
- **Demais tipos** (`refactor:`, `perf:`, `docs:`, `test:`, `ci:`, `chore:`) seguem o critério da correção; uma mudança de arquitetura segue a regra de feature.

A spec fica no corpo da issue e traz:

- **objetivo** e **não objetivos**;
- o **comportamento esperado**, com exemplos de entrada e saída;
- os **critérios de aceite**, com casos felizes e de erro;
- **riscos**, dependências e impacto em contratos, segurança e dados;
- o **plano de testes**.

Só comece a implementar depois de um mantenedor confirmar na issue que a spec está pronta. Se a implementação revelar que a spec estava errada, atualize a issue antes de mudar o PR.

O PR aponta para a issue com **uma linha só** no corpo: `Closes #N` (a issue fecha quando o PR é mesclado), `Refs #N` (o PR é uma parte de uma issue maior) ou `Spec: #N`. É a única referência a issue aceita no texto do PR.

## Fluxo de trabalho

1. Escolha uma issue ou abra uma com a spec, conforme a seção acima. As marcadas com `good first issue` e `help wanted` são as indicadas para quem está chegando; comente na issue antes de começar.
2. Crie uma branch a partir da base certa (veja abaixo).
3. Faça mudanças pequenas e focadas; cada commit usa Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `ci:`, `chore:`).
4. Abra o PR com título no mesmo formato, em português, e descreva o que muda e como você verificou. O título e a descrição não podem citar ferramentas de IA, planos de trabalho nem números de issue ou PR, exceto a linha de vínculo com a spec; o CI recusa.
5. Atenda à revisão. Um PR só é mesclado com os checks obrigatórios verdes e o aceite do CLA.

## Linhas de release

A configuração das linhas está em `.github/release-lines.json`.

| Linha                      | Branch base   | Milestone |
| -------------------------- | ------------- | --------- |
| Manutenção da série atual  | `master`      | `0.1.x`   |
| Desenvolvimento da próxima | `release/0.2` | `0.2`     |

## Plataformas

O Vectora oferece suporte a macOS somente em Apple Silicon (`arm64`). Não planejamos oferecer suporte a Macs Intel: a linha de hardware e sistema foi descontinuada pela Apple, e a cadeia de CI, dependências, sidecars e aplicações externas usadas pelo Vectora já não oferece uma base sustentável para mantê-la. Não adicione builds, testes ou documentação de suporte para macOS Intel.

Todo PR usa a base da linha que ele atinge e a milestone correspondente; o workflow `PR Release Milestone` atribui a milestone e bloqueia combinações incompatíveis. As versões são calculadas dos commits pelo workflow de release, que abre o PR `chore(master): release X.Y.Z`. Uma correção aplicada na linha de manutenção também precisa chegar à linha de desenvolvimento; o workflow de propagação abre esse PR.

## Mudanças em `.github/`

**Toda mudança em `.github/` (workflows, actions, configuração) precisa ser verificada com o `gh` simulado antes do PR**, e o PR diz quais cenários rodaram. Um workflow só pode ser testado de verdade no GitHub depois de mesclado; a simulação pega antes o erro que, de outro modo, só apareceria numa release.

O simulador (`utils/workflow_sim.py`) roda os passos `run:` reais de um job, em bash, com as expressões `${{ }}` resolvidas, e coloca no `PATH` um `gh` falso que:

- responde só ao que o cenário prevê; qualquer chamada sem regra falha com código 97, para um passo novo nunca passar em silêncio;
- registra cada chamada, com os argumentos e o token (`GH_TOKEN`) usado;
- imita a expansão de glob do `gh release upload` (colchetes, asteriscos e interrogações), que já derrubou uma release.

Passos `uses:` (actions) não são executados; prepare no diretório de trabalho os arquivos que eles produziriam.

O simulador executa comandos `run:` reais no host. Apenas as ferramentas que ele substitui são simuladas; isso **não é isolamento de segurança** para workflows não confiáveis. Não simule workflows recebidos de terceiros nem disponibilize segredos reais nos cenários. O subprocesso recebe um ambiente mínimo, com credenciais apenas quando elas estão declaradas no workflow ou no cenário; o `PATH` do host continua disponível para utilitários do sistema.

### Como rodar a simulação

Precisa de `bash`. No Linux e no macOS ele já existe; no Windows use o Git Bash (o simulador o procura em `C:\Program Files\Git\bin\bash.exe`; se estiver em outro lugar, defina `WORKFLOW_SIM_BASH`). O `bash` do WSL não serve.

```bash
uv run pytest tests/unit/test_workflow_sim.py tests/unit/test_release_workflow_simulation.py -q
```

Para experimentar um job com um cenário seu, sem escrever teste:

```bash
uv run python utils/workflow_sim.py .github/workflows/vectora.yml tag-release cenario.json
```

O cenário é um JSON com `context` (`github`, `secrets`, `needs`), `rules` (por ferramenta; `args` é um prefixo da chamada e `*` casa qualquer argumento) e `files` (nome e conteúdo dos arquivos a criar no diretório de trabalho):

```json
{
  "context": {
    "github": { "sha": "abc", "repository": "o/r", "token": "t" },
    "secrets": { "RELEASE_TAG_TOKEN": "" }
  },
  "rules": {
    "gh": [
      { "args": ["release", "view", "v1.0.0"], "exit": 1 },
      { "args": ["release", "create", "v1.0.0"] }
    ]
  },
  "files": { "dist-release/Vectora-1.0.0-win-x64.exe": "x" }
}
```

### Como cobrir uma mudança

1. Para cada passo novo ou alterado, escreva em `tests/unit/test_release_workflow_simulation.py` (ou num arquivo ao lado, no mesmo padrão) o cenário feliz e os de erro: crie os arquivos que o passo lê, declare com `Rule` cada chamada do `gh` que ele faz, rode o job com `run_job` e confira as chamadas, os tokens e os arquivos enviados.
2. Numa correção, o primeiro teste reproduz o erro que a motivou.
3. Rode a suíte acima e o `pre-commit` (ele inclui o `actionlint`).

A simulação confere a lógica do job: a ordem e os argumentos das chamadas, o token de cada uma e o que chega ao upload. Ela **não reproduz as regras do servidor do GitHub** (permissões do token, rulesets, limites de anexo). Isso só aparece numa execução real, então confira no PR o que depende dessas regras.

### Release: tag, release e anexos

- Os binários ficam **só na GitHub Release**. O Cloudflare recebe apenas o manifesto de atualização, que aponta para os arquivos da release. O job `tag-release` cria a tag, a release e os anexos; o `publish-update-channel` roda depois e publica o manifesto.
- O job anexa só os instaladores e os `.blockmap` deles, escolhidos pelo nome (`utils/release_assets.py`), mais o `SHA256SUMS`. Qualquer outro arquivo dos artefatos fica de fora.
- O corpo da release é a seção da versão no `CHANGELOG.md` (`utils/release_notes.py`); sem a seção, o GitHub gera as notas.
- Tag e release usam o secret `RELEASE_TAG_TOKEN`, se ele existir; sem ele, o `GITHUB_TOKEN`. O GitHub costuma recusar que o `GITHUB_TOKEN` crie uma tag cujo commit tenha workflows diferentes dos da branch padrão (erro `refusing to allow a GitHub App to create or update workflow`). Isso acontece quando a `master` muda um workflow depois do commit da release e o job é executado de novo. O job avisa quando detecta a divergência. O secret é um token fine-grained deste repositório, no ambiente `production`, com **Contents: Read and write** e **Workflows: Read and write**. Executar o job logo depois do merge funciona sem ele.

## O que o CI executa em PRs

Os workflows de PR rodam a versão do próprio PR. Eles validam título (Conventional Commits), ausência de referências proibidas no texto do PR, milestone, labels, lint, tipos, testes, varredura de segurança (Snyk) e o build dos instaladores. PRs de fork rodam sem segredos: rotulagem, milestone e comentários automáticos só valem para branches do próprio repositório.

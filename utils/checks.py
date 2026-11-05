"""Verificações do repositório usadas pelo pre-commit e pelos checks de PR.

Uso: `python utils/checks.py <comando> [args...]`. Só depende da stdlib, para
rodar no Python do runner sem instalar o ambiente do projeto.

Comandos:
    no-plan              Rejeita COMENTÁRIOS de código-fonte com proveniência de processo:
    pr-metadata          Rejeita TÍTULO/DESCRIÇÃO de PR com proveniência de processo: referência de
    removed-paths        Detect staged references to files removed or renamed by the commit
    translation-keys     Validate and deterministically sort Vectora translation message keys
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess  # nosec B404
import sys
import tokenize
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

# ── no-plan: Rejeita COMENTÁRIOS de código-fonte com proveniência de processo:
# referência de planejamento (Sprint/Bloco/Fase) ou menção a ferramenta/revisor
# de IA (CodeRabbit, Claude, Copilot, ChatGPT, etc). Comentários descrevem o
# que o código faz agora, não como ou por quem ele chegou nesse estado —
# histórico de planejamento/revisão mora em markdown ou na mensagem do commit,
# nunca em `.py`/`.ts`/`.tsx`/`.js`/`.jsx`. Roda como hook local de pre-commit,
# recebendo os arquivos staged como argv.
# Só comentários são varridos, nunca strings/identificadores — o Vectora tem
# strings/identificadores legítimos citando os mesmos nomes (ex.: ID de modelo
# `"claude-3-5-sonnet"`, detecção de harness `"claude_code"`); tratar string
# como comentário reintroduziria falso positivo em código de produção real.


#!/usr/bin/env python3


reconfigure = getattr(sys.stdout, "reconfigure", None)
if reconfigure is not None and sys.stdout.encoding.lower() != "utf-8":
    reconfigure(encoding="utf-8")

# Três formas usadas neste repo pra numerar etapas de plano: a primeira
# palavra de release ("SPRINT" em qualquer capitalização) seguida de um
# número OU de uma letra maiúscula única (planos deste repo já usaram as
# duas convenções — "Sprint 3" e "Sprint E" identificam etapa da mesma
# forma); a palavra de subdivisão de release ("PHASE", em português)
# seguida de um número (com sufixo de letra ou hífen opcional); e a
# palavra de bloco de trabalho ("BLOCK", em português) com "B" maiúsculo
# literal seguida de um identificador iniciado por maiúscula/dígito — sem
# essa última exigência, o regex bateria em qualquer uso comum do
# substantivo equivalente em português (como em "bloco de código"), que
# não tem nada a ver com plano. Só a palavra "sprint" em si ignora
# capitalização (`(?i:...)` escopado só a ela) — o identificador de letra
# que segue continua exigindo maiúscula, senão "sprint e atualiza" (texto
# comum, sem relação com plano) também bateria.
_PLAN_PATTERN: re.Pattern[str] = re.compile(
    r"(?i:\bsprint\s+)(?:\d+|[A-Z])\b|\bBloco\s+[A-Z][\w.]*\b|(?i:\bfase[\s-]+\d+[a-z]?\b)"
)

# Nome de ferramenta/revisor de IA — mesmo princípio do padrão de plano
# acima: comentário não é diário de quem/o-que produziu o código. Cada
# nome usa fronteira de palavra (\b) pra não colidir com substantivo comum
# (ex.: "copilot" sozinho já é o produto, sem risco de falso positivo real
# neste repo; "codex" também é específico o bastante). Case-insensitive —
# "CodeRabbit", "coderabbit", "CODERABBIT" são o mesmo problema. O lookahead
# negativo `(?!\.md)` exclui referência ao arquivo `CLAUDE.md` deste repo —
# citar esse arquivo (ex.: "CLAUDE.md §1") é proveniência de REGRA, não de
# ferramenta que gerou o código; sem a exceção, toda menção legítima ao
# arquivo de instruções do projeto seria bloqueada.
_AI_TOOL_PATTERN: re.Pattern[str] = re.compile(
    r"(?i:\b(?:coderabbit(?:ai)?|claude(?:\s*code)?|chatgpt|copilot|codex)\b)(?!\.md)"
)

# Proíbe referências a rastreadores de tarefas e a revisões abertas (issues e pull requests).
_TRACKER_PATTERN: re.Pattern[str] = re.compile(
    r"(?i:\b(?:issue|issues)\s*#?[1-9]\d*\b|#[1-9]\d*\b|\bpull\s+requests?(?:\s*#?[1-9]\d*)?\b|\b(?:pull\s*request|pr)\s*#?[1-9]\d*\b|https?://github\.com/[^\s/]+/[^\s/]+/(?:issues|pull)/[1-9]\d*)"
)

# Padrão único para metadados e comentários: planos artificiais,
# ferramentas de IA e referências a rastreadores.
_PR_METADATA_PATTERN: re.Pattern[str] = re.compile(
    f"{_PLAN_PATTERN.pattern}|{_AI_TOOL_PATTERN.pattern}|{_TRACKER_PATTERN.pattern}"
)
_PATTERN: re.Pattern[str] = _PR_METADATA_PATTERN


def _python_comments(text: str) -> list[tuple[int, str]]:
    """Executa a operação python comments."""
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        return [
            (tok.start[0], tok.string) for tok in tokens if tok.type == tokenize.COMMENT
        ]
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return []


def _js_comments(text: str) -> list[tuple[int, str]]:
    """Extrai comentários `//`/`/* */` de JS/TS/JSX ignorando o conteúdo de
    strings (aspas simples/duplas), mas SEM tratar `${...}` de template
    literal como string — é código de verdade (pode ter comentário/string/
    template aninhado), então uma pilha rastreia se estamos dentro de um
    template literal ("template") ou dentro de uma interpolação ("interp",
    com a profundidade de chaves pra achar o `}` de fechamento certo,
    já que a interpolação pode ter suas próprias chaves de objeto)."""
    out: list[tuple[int, str]] = []
    lineno = 1
    i = 0
    n = len(text)
    stack: list[tuple[str, int]] = []

    while i < n:
        ch = text[i]
        if ch == "\n":
            lineno += 1
            i += 1
            continue

        mode = stack[-1][0] if stack else "code"

        if mode == "template":
            if ch == "\\":
                if i + 1 < n and text[i + 1] == "\n":
                    lineno += 1
                i += 2
                continue
            if ch == "`":
                stack.pop()
                i += 1
                continue
            if ch == "$" and i + 1 < n and text[i + 1] == "{":
                stack.append(("interp", 1))
                i += 2
                continue
            i += 1
            continue

        # "code" (nível superior) e "interp" (dentro de `${...}`) usam a
        # mesma varredura de string/comentário/template aninhado — só
        # "interp" também rastreia chaves.
        if ch in ("'", '"'):
            quote = ch
            i += 1
            while i < n and text[i] != quote:
                if text[i] == "\\":
                    i += 1
                elif text[i] == "\n":
                    lineno += 1
                i += 1
            i += 1
            continue
        if ch == "`":
            stack.append(("template", 0))
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            end = text.find("\n", i)
            end = n if end == -1 else end
            out.append((lineno, text[i:end]))
            i = end
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            start_line = lineno
            end = text.find("*/", i + 2)
            fim_token = n if end == -1 else end + 2
            trecho = text[i:fim_token]
            out.append((start_line, trecho))
            lineno += trecho.count("\n")
            i = fim_token
            continue
        if mode == "interp":
            if ch == "{":
                nome, profundidade = stack[-1]
                stack[-1] = (nome, profundidade + 1)
                i += 1
                continue
            if ch == "}":
                nome, profundidade = stack[-1]
                if profundidade == 1:
                    stack.pop()
                else:
                    stack[-1] = (nome, profundidade - 1)
                i += 1
                continue
        i += 1
    return out


def _find_violations(path: str) -> list[tuple[int, str]]:
    """Executa a operação find violations."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []

    comments = _python_comments(text) if path.endswith(".py") else _js_comments(text)
    return [
        (lineno, comment.strip())
        for lineno, comment in comments
        if _PATTERN.search(comment)
    ]


def _changed_diff_ref() -> str:
    """Return the diff reference appropriate for local hooks or CI."""
    base_ref = os.environ.get("GITHUB_BASE_REF", "")
    if base_ref and re.fullmatch(r"[A-Za-z0-9._/-]+", base_ref):
        return f"origin/{base_ref}...HEAD"
    return "HEAD"


def no_plan_main(argv: list[str]) -> int:
    """Executa a operação main."""
    if "--changed" in argv:
        try:
            result = subprocess.run(  # nosec B603, B607
                [
                    "git",
                    "diff",
                    "--name-only",
                    "--diff-filter=AM",
                    _changed_diff_ref(),
                    "--",
                    "*.py",
                    "*.ts",
                    "*.tsx",
                    "*.js",
                    "*.jsx",
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        except (OSError, subprocess.CalledProcessError):
            return 1
        argv = [
            path
            for path in result.stdout.splitlines()
            if path
            and path
            not in {
                "utils/checks.py",
                "utils/test_checks.py",
            }
        ]
    had_violation = False
    for path in argv:
        for lineno, comment in _find_violations(path):
            had_violation = True
            print(f"{path}:{lineno}: proveniência de processo em comentário: {comment}")
    if had_violation:
        print(
            "\nComentários de código não referenciam Sprint/Bloco/Fase, ferramentas de IA "
            "(CodeRabbit, Claude, Copilot, etc), nem issues ou PRs. "
            "Reescreva descrevendo apenas o que o código faz atualmente — histórico de "
            "planejamento, issues e revisões mora em markdown, PR description ou na mensagem do commit."
        )
        return 1
    return 0


# ── pr-metadata: Rejeita TÍTULO/DESCRIÇÃO de PR com proveniência de processo: referência de
# planejamento (Sprint/Bloco/Fase) ou menção a ferramenta/revisor de IA
# (CodeRabbit, Claude, Copilot, ChatGPT, etc). Mesma regra de
# `no-plan` (comentários de código) — CLAUDE.md §1 cobre
# "qualquer artefato que entra no repositório", explicitamente incluindo
# "mensagens de PR". Diferente do hook de comentários (que roda local no
# pre-commit, arquivos staged como argv), este roda no CI, lendo título e
# corpo do evento `pull_request` via variáveis de ambiente.
# O corpo de um PR real acumula blocos auto-gerados pelo CodeRabbit (resumo,
# "Summary by CodeRabbit") que citam o próprio nome da ferramenta — não são
# proveniência de processo escrita por um humano, são o rodapé do bot. Esses
# blocos são delimitados por marcadores HTML fixos e removidos antes da
# varredura, senão todo PR revisado pelo CodeRabbit falharia sempre.


#!/usr/bin/env python3


# Blocos que o próprio CodeRabbit insere no corpo do PR (resumo de review,
# "Summary by CodeRabbit") — sempre delimitados por um comentário HTML de
# abertura "<!-- This is an auto-generated comment ... -->" e um de
# fechamento "<!-- end of auto-generated comment ... -->". Non-greedy +
# DOTALL: o corpo pode ter múltiplos blocos desses, cada um contido entre
# seu próprio par de marcadores.
_AUTO_GENERATED_BLOCK = re.compile(
    r"<!--\s*This is an auto-generated comment.*?-->.*?"
    r"<!--\s*end of auto-generated comment.*?-->",
    re.DOTALL,
)


def _strip_bot_blocks(body: str) -> str:
    """Executa a operação strip bot blocks."""
    return _AUTO_GENERATED_BLOCK.sub("", body)


# Linha só com o vínculo à issue que traz a spec (`Closes #12`, `Refs #12`,
# `Spec: #12` ou a URL da issue). É a única referência a rastreador aceita no
# corpo do PR; o título e qualquer outra linha continuam sob a regra geral.
_ISSUE_LINK_LINE = re.compile(
    r"^\s*(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?|spec)\s*:?\s+"
    r"(?:#[1-9]\d*|https://github\.com/[^\s/]+/[^\s/]+/issues/[1-9]\d*)\s*$",
    re.IGNORECASE,
)


def _strip_issue_links(body: str) -> str:
    """Remove as linhas que são só o vínculo com a issue da spec."""
    return "\n".join(
        line for line in body.splitlines() if not _ISSUE_LINK_LINE.match(line)
    )


def pr_metadata_main() -> int:
    """Executa a operação main."""
    title = os.environ.get("PR_TITLE", "")
    body = _strip_issue_links(_strip_bot_blocks(os.environ.get("PR_BODY", "") or ""))

    violations: list[tuple[str, str]] = []
    if _PR_METADATA_PATTERN.search(title):
        violations.append(("título", title.strip()))
    for line in body.splitlines():
        if _PR_METADATA_PATTERN.search(line):
            violations.append(("descrição", line.strip()))

    if not violations:
        return 0

    for field, text in violations:
        print(f"PR {field}: proveniência de processo encontrada: {text}")
    print(
        "\nTítulo/descrição do PR não podem referenciar Sprint/Bloco/Fase nem "
        "ferramenta/revisor de IA (CodeRabbit, Claude, Copilot, etc) — mesma "
        "regra dos comentários de código (CLAUDE.md §1, que cobre também "
        "mensagens de PR). Descreva só o que a mudança faz; histórico de "
        "planejamento deve ficar em .claude/plans/ ou na issue/discussão apropriada. "
        "A única referência a issue aceita no corpo é o vínculo com a spec, "
        "numa linha só (`Closes #N`, `Refs #N` ou `Spec: #N`)."
    )
    return 1


# ── removed-paths: Detect staged references to files removed or renamed by the commit
# The check reads the Git index so it validates the exact snapshot that will be
# committed, including staged edits and renames.  It intentionally reports path
# mentions rather than trying to infer every language's import semantics.


#!/usr/bin/env python3


@dataclass(frozen=True)
class RemovedPath:
    """A path that will no longer exist after the staged commit."""

    path: str
    replacement: str | None = None


def _git_output(*args: str) -> bytes:
    result = subprocess.run(  # nosec B603, B607
        ["git", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def _staged_removed_paths(raw: bytes) -> list[RemovedPath]:
    """Parse ``git diff --name-status -z`` deletion and rename records."""
    fields = raw.split(b"\0")
    removed: list[RemovedPath] = []
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index].decode("ascii")
        index += 1
        if status.startswith("R"):
            if index + 1 >= len(fields):
                break
            old = fields[index].decode("utf-8", errors="surrogateescape")
            new = fields[index + 1].decode("utf-8", errors="surrogateescape")
            removed.append(RemovedPath(old, new))
            index += 2
        elif status == "D":
            if index >= len(fields):
                break
            removed.append(
                RemovedPath(fields[index].decode("utf-8", errors="surrogateescape"))
            )
            index += 1
        else:
            index += 1
    return removed


def _normalise_path(path: str) -> str:
    return str(PurePosixPath(path.replace("\\", "/")))


def _safe_terminal_text(value: str) -> str:
    """Escape characters unsupported by the current Windows console encoding."""
    encoding = sys.stdout.encoding
    if encoding is None:
        return value
    return value.encode(encoding, errors="backslashreplace").decode(encoding)


def _find_references(
    files: dict[str, str], removed: list[RemovedPath]
) -> list[tuple[str, int, str, str]]:
    """Return ``(file, line, removed_path, line_text)`` for path mentions."""
    references: list[tuple[str, int, str, str]] = []
    removed_paths = {_normalise_path(item.path): item for item in removed}
    for filename, text in files.items():
        if filename in removed_paths:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            normalised_line = line.replace("\\", "/")
            for path in removed_paths:
                if path in normalised_line:
                    references.append((filename, line_number, path, line.strip()))
    return references


def _staged_files() -> dict[str, str]:
    files: dict[str, str] = {}
    for entry in _git_output("ls-files", "-s", "-z").split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        if metadata.split(b" ", 1)[0] == b"160000":
            continue
        path = raw_path.decode("utf-8", errors="surrogateescape")
        blob = _git_output("show", f":{path}")
        if b"\0" in blob:
            continue
        try:
            files[path] = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return files


def removed_paths_main() -> int:
    try:
        removed = _staged_removed_paths(
            _git_output(
                "diff",
                "--cached",
                "--name-status",
                "-z",
                "-M",
                "--diff-filter=DR",
            )
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"removed-path-references: unable to inspect Git index: {exc}")
        return 1

    if not removed:
        return 0

    references = _find_references(_staged_files(), removed)
    if not references:
        return 0

    print("References to staged removed or renamed paths were found:")
    for filename, line_number, removed_path, line in references:
        print(
            _safe_terminal_text(f"  {filename}:{line_number}: {removed_path} <- {line}")
        )
    print(
        "Update or remove these references before committing. "
        "Renamed paths may use the replacement path shown in the diff."
    )
    return 1


# ── translation-keys: Validate and deterministically sort Vectora translation message keys


METADATA_KEYS: Final = ("$schema", "$comment")
DEFAULT_FILES: Final = tuple(sorted(Path("frontend/messages").glob("*.json")))


def sorted_messages(data: dict[str, object]) -> dict[str, object]:
    """Keep metadata first and sort every translation key lexicographically."""
    metadata = {key: data[key] for key in METADATA_KEYS if key in data}
    messages = {key: data[key] for key in data if key not in metadata}
    return {**metadata, **dict(sorted(messages.items(), key=lambda item: item[0]))}


def check_file(path: Path, *, write: bool) -> bool:
    """Return whether a file is ordered; optionally rewrite it."""
    original = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(original, dict):
        raise ValueError(f"arquivo de traduções inválido: {path}")
    ordered = sorted_messages(original)
    content = json.dumps(ordered, ensure_ascii=False, indent=2) + "\n"
    if list(original) == list(ordered):
        return True
    if write:
        path.write_text(content, encoding="utf-8")
        return True
    print(f"chaves de tradução fora de ordem: {path}")
    return False


def translation_keys_main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    paths = tuple(args.paths) or DEFAULT_FILES
    results = [check_file(path, write=args.write) for path in paths]
    return 0 if all(results) else 1


# ── CLI ─────────────────────────────────────────────────────────────────────


_COMMANDS: dict[str, tuple[Callable[..., int], bool]] = {
    "no-plan": (no_plan_main, True),
    "pr-metadata": (pr_metadata_main, False),
    "removed-paths": (removed_paths_main, False),
    "translation-keys": (translation_keys_main, False),
}


def main(argv: Sequence[str] | None = None) -> int:
    """Despacha `checks.py <comando> [args...]` para o subcomando.

    Subcomandos sem argv explícito leem `sys.argv` como se fossem o script
    original: `sys.argv[0]` vira `"checks.py <comando>"` e o resto segue igual.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in _COMMANDS:
        print(f"usage: checks.py {{{'|'.join(_COMMANDS)}}} [args...]", file=sys.stderr)
        return 2
    command, rest = args[0], args[1:]
    handler, takes_argv = _COMMANDS[command]
    if takes_argv:
        return handler(rest)
    sys.argv = [f"checks.py {command}", *rest]
    return handler()


if __name__ == "__main__":
    raise SystemExit(main())

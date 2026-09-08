#!/usr/bin/env python3
"""Rejeita COMENTÁRIOS de código-fonte com proveniência de processo:
referência de planejamento (Sprint/Bloco/Fase) ou menção a ferramenta/revisor
de IA (CodeRabbit, Claude, Copilot, ChatGPT, etc). Comentários descrevem o
que o código faz agora, não como ou por quem ele chegou nesse estado —
histórico de planejamento/revisão mora em markdown ou na mensagem do commit,
nunca em `.py`/`.ts`/`.tsx`/`.js`/`.jsx`. Roda como hook local de pre-commit,
recebendo os arquivos staged como argv.

Só comentários são varridos, nunca strings/identificadores — o Vectora tem
strings/identificadores legítimos citando os mesmos nomes (ex.: ID de modelo
`"claude-3-5-sonnet"`, detecção de harness `"claude_code"`); tratar string
como comentário reintroduziria falso positivo em código de produção real.
"""

from __future__ import annotations

import io
import os
import re
import subprocess  # nosec B404
import sys
import tokenize

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

_PATTERN: re.Pattern[str] = re.compile(
    f"{_PLAN_PATTERN.pattern}|{_AI_TOOL_PATTERN.pattern}"
)


def _python_comments(text: str) -> list[tuple[int, str]]:
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


def main(argv: list[str]) -> int:
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
                "utils/check_no_plan_comments.py",
                "utils/test_check_no_plan_comments.py",
                "utils/check_no_plan_pr_metadata.py",
                "utils/test_check_no_plan_pr_metadata.py",
            }
        ]
    had_violation = False
    for path in argv:
        for lineno, comment in _find_violations(path):
            had_violation = True
            print(f"{path}:{lineno}: proveniência de processo em comentário: {comment}")
    if had_violation:
        print(
            "\nComentários de código não referenciam Sprint/Bloco/Fase nem "
            "ferramenta/revisor de IA (CodeRabbit, Claude, Copilot, etc). "
            "Reescreva descrevendo só o estado atual do código — histórico de "
            "planejamento/revisão vai em docs/, .claude/plans/ ou na mensagem "
            "do commit."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

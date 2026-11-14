"""Detecta código, páginas e rotas que nada alcança.

Uso: `python utils/unused_code.py <comando>`. Só depende da stdlib, exceto o
comando `python`, que precisa do `vulture` no ambiente (`uv run --with vulture`).

Comandos:
    routes     Páginas (rotas do frontend) que nenhuma navegação do código nem
               uma entrada externa declarada alcança
    frontend   Arquivos, dependências e exports do frontend sem uso (knip)
    python     Funções, métodos e classes Python sem uso (vulture), contra o
               baseline em utils/unused_python_baseline.json

O que o knip não vê (rotas do TanStack Router, símbolos Python registrados por
decorador) tem a detecção própria deste arquivo. Entradas externas legítimas de
uma página (link público, retorno de OAuth) ficam declaradas, com o motivo, em
utils/route_entrypoints.json: uma rota sem navegação e sem declaração é apontada.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import subprocess  # nosec B404
import sys
import tokenize
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parent.parent
FRONTEND: Final = ROOT / "frontend"
ROUTES_DIR: Final = FRONTEND / "src" / "routes"
ENTRYPOINTS: Final = ROOT / "utils" / "route_entrypoints.json"
PYTHON_BASELINE: Final = ROOT / "utils" / "unused_python_baseline.json"

#: Diretórios de código de produção onde uma navegação conta como referência.
FRONTEND_SOURCE_DIRS: Final = ("components", "lib", "src", "hooks", "electron/src")
FRONTEND_SUFFIXES: Final = {".ts", ".tsx", ".mts"}
#: Caminhos que não são código de produção.
_NOT_PRODUCTION: Final = re.compile(
    r"(^|/)(__tests__|tests|node_modules|dist|paraglide)(/|$)|\.test\.[tj]sx?$|routeTree\.gen\.ts$"
)

#: Texto que precede o literal e mostra que é uma navegação de página, não um
#: `fetch` de API: `to:`, `href=`, `navigate(`, `router.replace(`, `location.href =`.
_FRONTEND_NAV_CONTEXT: Final = re.compile(
    r"(?:\bto\s*[:=]\s*\{?"
    r"|\bhref\s*[:=]\s*\{?"
    r"|\b(?:navigate|redirect|Navigate)\b[^;]{0,40}"
    r"|\.(?:push|replace|assign)\(\s*"
    r"|\blocation(?:\.href)?\s*=\s*"
    r"|\bwindow\.open\(\s*)$"
)
_PYTHON_NAV_CONTEXT: Final = re.compile(
    r"(?:RedirectResponse\(\s*(?:url\s*=\s*)?|\bredirect(?:_to|_url)?\s*[=(]\s*|\bLocation\W+)$",
    re.IGNORECASE,
)


# ── Rotas do frontend ───────────────────────────────────────────────────────


def load_routes(routes_dir: Path = ROUTES_DIR) -> list[str]:
    """Caminhos completos das páginas, lidos da árvore de arquivos de `src/routes`.

    Segue as convenções do TanStack Router: `index.tsx` é o `/` da pasta, `$param`
    é segmento dinâmico, ponto no nome vira barra, e pastas `_layout` ou
    `(grupo)` não entram no caminho. O `__root.tsx` e os testes não são páginas.
    Lê os arquivos em vez de `routeTree.gen.ts`, que é gerado e não é versionado.
    """
    routes: list[str] = []
    for file in sorted(routes_dir.rglob("*")):
        relative = file.relative_to(routes_dir)
        if file.suffix not in {".ts", ".tsx"} or "__tests__" in relative.parts:
            continue
        parts = relative.with_suffix("").parts
        # `-arquivo` e `-pasta` são auxiliares ignorados pelo router; `__root` e
        # `_layout.tsx` não têm caminho próprio.
        if parts[-1].startswith(("_", "-")) or any(p.startswith("-") for p in parts):
            continue
        segments = [
            segment
            for part in parts
            for segment in part.split(".")
            if not segment.startswith("_") and not segment.startswith("(")
        ]
        is_index = bool(segments) and segments[-1] == "index"
        if is_index:
            segments = segments[:-1]
        path = "/" + "/".join(segments)
        if is_index and segments:
            path += "/"
        routes.append(path)
    return routes


def _normalize(path: str) -> str:
    return path.rstrip("/") or "/"


def route_pattern(route: str) -> re.Pattern[str]:
    """Regex que casa `/session/new`, `/session/${id}` ou `/session/$threadId`."""
    segments = [seg for seg in _normalize(route).split("/") if seg]
    parts = [
        r"(?:\$\w+|\$\{[^}]*\}|[^/?#]+)" if seg.startswith("$") else re.escape(seg)
        for seg in segments
    ]
    body = "/" + "/".join(parts) if parts else "/"
    return re.compile(rf"^{body}/?(?:[?#].*)?$")


def literal_targets_route(literal: str, route: str) -> bool:
    """O literal de string aponta para a rota (inclusive prefixo `"/session/" + id`)."""
    if route_pattern(route).match(literal):
        return True
    return (
        literal.endswith("/")
        and literal != "/"
        and _normalize(route).startswith(_normalize(literal) + "/")
    )


def _string_literals_ts(source: str) -> Iterator[tuple[str, str]]:
    """Pares (literal, texto anterior) do código TS, sem comentários.

    Percorre o texto como um tokenizador mínimo: ignora `//` e `/* */` e entende
    aspas simples, duplas e crases (o conteúdo da crase é mantido com os `${}`).
    """
    length = len(source)
    index = 0
    code: list[str] = []
    while index < length:
        char = source[index]
        if source.startswith("//", index):
            index = source.find("\n", index)
            index = length if index < 0 else index
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        if char in "'\"`":
            end = index + 1
            while end < length and source[end] != char:
                end += 2 if source[end] == "\\" else 1
            literal = source[index + 1 : end]
            yield literal, "".join(code)[-120:]
            code.append("\x00")
            index = end + 1
            continue
        code.append(char)
        index += 1


def _string_literals_py(source: str) -> Iterator[tuple[str, str]]:
    """Pares (literal, texto anterior) do código Python, sem comentários."""
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    previous: list[str] = []
    for token in tokens:
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING:
            literal = token.string.lstrip("rbfuRBFU").strip("'\"")
            yield literal, "".join(previous)[-120:]
        if token.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT):
            previous.append(token.string)


def _iter_files(roots: Sequence[Path], suffixes: set[str]) -> Iterator[Path]:
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(ROOT).as_posix()
            if path.suffix in suffixes and not _NOT_PRODUCTION.search(relative):
                yield path


def find_route_references(
    routes: Sequence[str],
    frontend_dirs: Sequence[Path],
    python_dirs: Sequence[Path],
) -> dict[str, list[str]]:
    """Arquivos de produção que navegam para cada rota."""
    found: dict[str, list[str]] = {route: [] for route in routes}
    candidates: list[tuple[Path, Iterator[tuple[str, str]], re.Pattern[str]]] = []
    for path in _iter_files(frontend_dirs, FRONTEND_SUFFIXES):
        source = path.read_text(encoding="utf-8", errors="replace")
        candidates.append((path, _string_literals_ts(source), _FRONTEND_NAV_CONTEXT))
    for path in _iter_files(python_dirs, {".py"}):
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            candidates.append((path, _string_literals_py(source), _PYTHON_NAV_CONTEXT))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            continue
    for path, literals, context in candidates:
        relative = path.relative_to(ROOT).as_posix()
        try:
            for literal, before in literals:
                if not literal.startswith("/") or not context.search(before):
                    continue
                for route in routes:
                    if literal_targets_route(literal, route) and (
                        relative not in found[route]
                    ):
                        found[route].append(relative)
        except (tokenize.TokenError, IndentationError, SyntaxError):
            continue
    return found


def load_entrypoints(path: Path = ENTRYPOINTS) -> dict[str, str]:
    """Rotas que têm entrada externa declarada, com o motivo."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(
        isinstance(key, str) and isinstance(value, str) and value.strip()
        for key, value in data.items()
    ):
        raise ValueError(f"{path}: esperado {{rota: motivo}} com motivo não vazio")
    return data


def unreachable_routes(
    routes: Sequence[str],
    references: dict[str, list[str]],
    entrypoints: dict[str, str],
) -> tuple[list[str], list[str]]:
    """(rotas sem navegação nem entrada declarada, entradas declaradas obsoletas).

    Uma entrada declarada para uma rota que não existe mais, ou que já tem
    navegação no código, é obsoleta: a declaração precisa sair.
    """
    normalized = {_normalize(route): route for route in routes}
    unreachable = [
        route for route in routes if not references[route] and route not in entrypoints
    ]
    stale = [
        route
        for route in entrypoints
        if route not in normalized.values() or references.get(route)
    ]
    return unreachable, stale


def routes_main(_argv: Sequence[str] = ()) -> int:
    routes = load_routes()
    references = find_route_references(
        routes,
        [FRONTEND / name for name in FRONTEND_SOURCE_DIRS],
        [ROOT / "backend"],
    )
    entrypoints = load_entrypoints()
    unreachable, stale = unreachable_routes(routes, references, entrypoints)
    for route in unreachable:
        print(
            f"rota sem navegação: {route}\n"
            "  nenhum código navega para ela. Remova a página ou declare a entrada "
            "externa e o motivo em utils/route_entrypoints.json."
        )
    for route in stale:
        print(
            f"entrada declarada obsoleta: {route}\n"
            "  a rota não existe mais ou já tem navegação no código; remova-a de "
            "utils/route_entrypoints.json."
        )
    return 1 if unreachable or stale else 0


# ── Frontend (knip) ─────────────────────────────────────────────────────────


def _pnpm() -> str | None:
    return shutil.which("pnpm")


def frontend_main(_argv: Sequence[str] = ()) -> int:
    """Roda o knip em modo produção (arquivos e dependências) e no padrão (exports)."""
    pnpm = _pnpm()
    if pnpm is None:
        print("unused-code: pnpm não encontrado no PATH", file=sys.stderr)
        return 2
    runs = (
        ["--production", "--include", "files,dependencies"],
        ["--include", "exports,types,dependencies"],
    )
    status = 0
    for extra in runs:
        result = subprocess.run(  # nosec B603
            [pnpm, "--dir", str(FRONTEND), "exec", "knip", "--no-progress", *extra],
            check=False,
        )
        status = status or result.returncode
    return status


# ── Python (vulture) ────────────────────────────────────────────────────────

_VULTURE_LINE: Final = re.compile(
    r"^(?P<file>[^:]+):\d+: unused (?P<kind>function|method|class) "
    r"'(?P<name>[^']+)' \((?P<confidence>\d+)% confidence\)$"
)
VULTURE_IGNORE_DECORATORS: Final = (
    "@*router.*,@*app.*,@*.route,@*.get,@*.post,@*.put,@*.patch,@*.delete,@*.websocket,@*.on_event,@*.exception_handler,@*.middleware,@pytest.*,@property,@*.setter,@abstractmethod,"
    "@validator,@field_validator,@model_validator,@computed_field,@tool,@hookimpl,"
    "@asynccontextmanager,@staticmethod,@classmethod,@override,@overload"
)
VULTURE_IGNORE_NAMES: Final = "model_config,model_post_init,lifespan"


def parse_vulture(output: str) -> list[str]:
    """Chaves estáveis `arquivo::tipo::nome` (sem linha, para não mudar a cada edição)."""
    keys = set()
    for line in output.splitlines():
        match = _VULTURE_LINE.match(line.strip())
        if match:
            keys.add(f"{match['file']}::{match['kind']}::{match['name']}")
    return sorted(keys)


def compare_baseline(
    current: Sequence[str], baseline: Sequence[str]
) -> tuple[list[str], list[str]]:
    """(achados novos, entradas do baseline que já não aparecem)."""
    return (
        sorted(set(current) - set(baseline)),
        sorted(set(baseline) - set(current)),
    )


def python_main(argv: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser(prog="unused_code.py python")
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="grava o estado atual como baseline (use só ao remover código)",
    )
    args = parser.parse_args(list(argv))
    result = subprocess.run(  # nosec B603
        [
            sys.executable,
            "-m",
            "vulture",
            "backend",
            "--min-confidence",
            "60",
            "--ignore-decorators",
            VULTURE_IGNORE_DECORATORS,
            "--ignore-names",
            VULTURE_IGNORE_NAMES,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 3):
        print(result.stderr or result.stdout, file=sys.stderr)
        return 2
    current = parse_vulture(result.stdout)
    if args.write_baseline:
        PYTHON_BASELINE.write_text(
            json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"baseline gravado: {len(current)} entradas")
        return 0
    baseline = (
        json.loads(PYTHON_BASELINE.read_text(encoding="utf-8"))
        if PYTHON_BASELINE.exists()
        else []
    )
    new, gone = compare_baseline(current, baseline)
    for key in new:
        print(f"código Python sem uso: {key}\n  remova-o ou use-o.")
    for key in gone:
        print(
            f"baseline obsoleto: {key}\n"
            "  já não aparece como sem uso; rode "
            "`python utils/unused_code.py python --write-baseline`."
        )
    return 1 if new or gone else 0


# ── CLI ─────────────────────────────────────────────────────────────────────

_COMMANDS: Final[dict[str, Callable[[Sequence[str]], int]]] = {
    "routes": routes_main,
    "frontend": frontend_main,
    "python": python_main,
}


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in _COMMANDS:
        print(__doc__)
        return 2
    return _COMMANDS[arguments[0]](arguments[1:])


if __name__ == "__main__":
    raise SystemExit(main())

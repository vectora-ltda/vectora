#!/usr/bin/env python3
"""Simula jobs de workflows do GitHub Actions com um `gh` (e outras ferramentas) falso.

Roda os passos `run:` REAIS de um job, em bash, com as expressões `${{ }}`
resolvidas a partir de um contexto, e coloca no PATH um `gh` falso que:

- responde só ao que o cenário prevê e falha (código 97) em qualquer chamada
  sem regra, para um passo novo nunca passar em silêncio;
- registra cada chamada, com os argumentos e o token (`GH_TOKEN`) usado;
- imita a expansão de glob do `gh release upload` (colchetes, asteriscos e
  interrogações), que já derrubou uma release: `no matches found for ...`.

Passos `uses:` (actions) não são executados; prepare no `workdir` os arquivos
que eles produziriam. Toda mudança em `.github/` deve ser conferida aqui antes
do PR (ver CONTRIBUTING.md).

Uso:  python utils/workflow_sim.py WORKFLOW JOB CENARIO.json

O cenário é um JSON com `context` (github, secrets, needs, env), `rules` (por
ferramenta: `args` é um prefixo, `*` casa qualquer argumento) e `files` (nome e
conteúdo dos arquivos a criar no diretório de trabalho).
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess  # nosec B404
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class UnsupportedExpression(ValueError):
    """Expressão `${{ }}` que o simulador ainda não sabe avaliar."""


# ── expressões ${{ }} ──────────────────────────────────────────────────────

_TOKEN = re.compile(
    r"\s*(\|\||&&|==|!=|!|\(|\)|,|'[^']*'|[A-Za-z_][\w.\-]*|\d+(?:\.\d+)?)"
)
_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)


def _tokenize(source: str) -> list[str]:
    """Converte a expressão do Actions em tokens e rejeita sintaxe desconhecida."""
    tokens: list[str] = []
    position = 0
    while position < len(source):
        if source[position:].strip() == "":
            break
        match = _TOKEN.match(source, position)
        if match is None:
            raise UnsupportedExpression(f"Expressão não suportada: {source!r}")
        tokens.append(match.group(1))
        position = match.end()
    return tokens


def _lookup(path: str, context: Mapping[str, Any]) -> Any:
    """Resolve um caminho pontuado no contexto, usando string vazia se faltar."""
    value: Any = context
    for part in path.split("."):
        if isinstance(value, Mapping) and part in value:
            value = value[part]
        else:
            return ""
    return value


def _truthy(value: Any) -> bool:
    return value not in ("", None, False, 0)


class _Parser:
    def __init__(self, tokens: list[str], context: Mapping[str, Any], source: str):
        self.tokens = tokens
        self.context = context
        self.source = source
        self.index = 0

    def _peek(self) -> str | None:
        """Retorna o próximo token sem avançar o cursor."""
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _take(self) -> str:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def parse(self) -> Any:
        """Avalia a expressão completa e rejeita tokens que sobrarem."""
        value = self._or()
        if self._peek() is not None:
            raise UnsupportedExpression(f"Expressão não suportada: {self.source!r}")
        return value

    def _or(self) -> Any:
        """Avalia `||` com a semântica de fallback do Actions."""
        value = self._and()
        while self._peek() == "||":
            self._take()
            right = self._and()
            value = value if _truthy(value) else right
        return value

    def _and(self) -> Any:
        """Avalia `&&` mantendo a semântica lógica do Actions."""
        value = self._equality()
        while self._peek() == "&&":
            self._take()
            right = self._equality()
            value = right if _truthy(value) else value
        return value

    def _equality(self) -> Any:
        """Compara operandos com `==` e `!=` sem distinção de maiúsculas."""
        value = self._unary()
        while self._peek() in ("==", "!="):
            operator = self._take()
            right = self._unary()
            same = str(value).lower() == str(right).lower()
            value = same if operator == "==" else not same
        return value

    def _unary(self) -> Any:
        """Aplica a negação lógica ou delega para a expressão primária."""
        if self._peek() == "!":
            self._take()
            return not _truthy(self._unary())
        return self._primary()

    def _primary(self) -> Any:
        """Lê literais, referências, chamadas e expressões entre parênteses."""
        lexeme = self._peek()
        if lexeme is None:
            raise UnsupportedExpression(f"Expressão incompleta: {self.source!r}")
        self._take()
        if lexeme == "(":
            value = self._or()
            if self._peek() != ")":
                raise UnsupportedExpression(f"Parêntese aberto: {self.source!r}")
            self._take()
            return value
        if lexeme.startswith("'"):
            return lexeme[1:-1]
        if re.fullmatch(r"\d+(?:\.\d+)?", lexeme):
            return float(lexeme) if "." in lexeme else int(lexeme)
        if lexeme in ("true", "false", "null"):
            return {"true": True, "false": False, "null": None}[lexeme]
        if self._peek() == "(":
            return self._call(lexeme)
        return _lookup(lexeme, self.context)

    def _call(self, name: str) -> Any:
        """Executa uma das funções de expressão explicitamente suportadas."""
        self._take()  # "("
        args: list[Any] = []
        while self._peek() != ")":
            args.append(self._or())
            if self._peek() == ",":
                self._take()
        self._take()  # ")"
        functions = {
            "always": lambda: True,
            "success": lambda: True,
            "failure": lambda: False,
            "cancelled": lambda: False,
            "contains": lambda a, b: str(b).lower() in str(a).lower(),
            "startswith": lambda a, b: str(a).lower().startswith(str(b).lower()),
            "endswith": lambda a, b: str(a).lower().endswith(str(b).lower()),
        }
        function = functions.get(name.lower())
        if function is None:
            raise UnsupportedExpression(f"Função não suportada: {name}()")
        return function(*args)


def evaluate(expression: str, context: Mapping[str, Any]) -> Any:
    """Avalia uma expressão do Actions (sem o `${{ }}`)."""
    return _Parser(_tokenize(expression), context, expression).parse()


def evaluate_condition(expression: str, context: Mapping[str, Any]) -> bool:
    """Avalia o `if:` de um passo."""
    match = _EXPRESSION.fullmatch(expression.strip())
    return _truthy(evaluate(match.group(1) if match else expression, context))


def _stringify(value: Any) -> str:
    """Converte valores ao formato textual interpolado pelo Actions."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render(text: str, context: Mapping[str, Any]) -> str:
    """Substitui cada `${{ expressão }}` pelo valor, como o Actions faz."""
    return _EXPRESSION.sub(
        lambda m: _stringify(evaluate(m.group(1).strip(), context)), text
    )


# ── ferramentas falsas ─────────────────────────────────────────────────────

_FAKE_TOOL = r"""
import glob, json, os, sys, time

tool, args = sys.argv[1], sys.argv[2:]
state = os.environ["WORKFLOW_SIM_DIR"]
entry = {"t": time.time_ns(), "tool": tool, "args": args,
         "token": os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""}


def finish(code, out="", err=""):
    # Um arquivo por chamada: `diff <(gh ...) <(gh ...)` roda dois processos ao
    # mesmo tempo, e escrever no mesmo arquivo misturaria as linhas.
    name = "%d-%d.json" % (entry["t"], os.getpid())
    with open(os.path.join(state, "calls", name), "w", encoding="utf-8") as log:
        log.write(json.dumps(entry))
    sys.stdout.write(out)
    sys.stderr.write(err)
    sys.exit(code)


if tool == "gh" and args[:2] == ["release", "upload"]:
    positional, skip = [], False
    for arg in args[2:]:
        if skip:
            skip = False
        elif arg in ("--repo", "-R"):
            skip = True
        elif not arg.startswith("-"):
            positional.append(arg)
    expanded = []
    for pattern in positional[1:]:
        base = pattern.partition("#")[0]
        found = sorted(glob.glob(base))
        if not found:
            finish(1, err="no matches found for `%s`\n" % base)
        expanded += [name.replace("\\", "/") for name in found]
    entry["files"] = expanded

with open(os.path.join(state, "rules-%s.json" % tool), encoding="utf-8") as handle:
    rules = json.load(handle)
for rule in rules:
    pattern = rule["args"]
    if len(pattern) <= len(args) and all(
        want == "*" or want == got for want, got in zip(pattern, args)
    ):
        finish(rule.get("exit", 0), rule.get("stdout", ""), rule.get("stderr", ""))
finish(97, err="%s-sim: chamada sem regra: %s %s\n" % (tool, tool, " ".join(args)))
"""


@dataclass(frozen=True)
class Rule:
    """Resposta de uma ferramenta falsa; `args` é um prefixo (`*` casa qualquer um)."""

    args: list[str]
    stdout: str = ""
    stderr: str = ""
    exit: int = 0

    def to_json(self) -> dict[str, Any]:
        """Serializa a regra para o processo da ferramenta falsa."""
        return {
            "args": self.args,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit": self.exit,
        }


@dataclass(frozen=True)
class Call:
    """Chamada registrada de uma ferramenta falsa."""

    tool: str
    args: list[str]
    token: str
    files: list[str] = field(default_factory=list)


class Sandbox:
    """Diretório com as ferramentas falsas e o registro das chamadas."""

    def __init__(self, root: Path, tools: Sequence[str] = ("gh",)) -> None:
        """Cria os shims e o diretório isolado que registra chamadas simuladas."""
        self.root = Path(root)
        self.bin = self.root / "bin"
        self.state = self.root / "state"
        self.bin.mkdir(parents=True, exist_ok=True)
        (self.state / "calls").mkdir(parents=True, exist_ok=True)
        script = self.bin / "_fake_tool.py"
        script.write_text(_FAKE_TOOL, encoding="utf-8", newline="\n")
        for tool in tools:
            shim = self.bin / tool
            shim.write_text(
                "#!/usr/bin/env bash\n"
                f'export WORKFLOW_SIM_DIR="{self.state.as_posix()}"\n'
                f'exec "{Path(sys.executable).as_posix()}" "{script.as_posix()}" '
                f'{tool} "$@"\n',
                encoding="utf-8",
                newline="\n",
            )
            shim.chmod(0o755)
            self.set_rules(tool, [])
        # Os runners do Actions têm `python3`; no Windows ele não existe com esse nome.
        alias = self.bin / "python3"
        alias.write_text(
            f'#!/usr/bin/env bash\nexec "{Path(sys.executable).as_posix()}" "$@"\n',
            encoding="utf-8",
            newline="\n",
        )
        alias.chmod(0o755)

    def set_rules(self, tool: str, rules: Sequence[Rule]) -> None:
        """Substitui as respostas permitidas para uma ferramenta no cenário."""
        (self.state / f"rules-{tool}.json").write_text(
            json.dumps([rule.to_json() for rule in rules]), encoding="utf-8"
        )

    def calls(self, tool: str | None = None) -> list[Call]:
        """Lê chamadas registradas em ordem e opcionalmente filtra por ferramenta."""
        entries = sorted(
            (
                json.loads(path.read_text(encoding="utf-8"))
                for path in (self.state / "calls").glob("*.json")
            ),
            key=lambda entry: entry["t"],
        )
        calls = [
            Call(
                tool=entry["tool"],
                args=entry["args"],
                token=entry.get("token", ""),
                files=entry.get("files", []),
            )
            for entry in entries
        ]
        return [call for call in calls if tool is None or call.tool == tool]

    def uploaded_files(self) -> list[str]:
        """Arquivos que as chamadas `gh release upload` receberam, já expandidos."""
        return [
            name
            for call in self.calls("gh")
            if call.args[:2] == ["release", "upload"]
            for name in call.files
        ]


# ── execução do job ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StepResult:
    name: str | None
    exit_code: int
    output: str
    skipped: str | None = None


def find_bash() -> str:
    """Bash real (no Windows, o Git Bash; o `bash` do PATH pode ser o do WSL)."""
    override = os.environ.get("WORKFLOW_SIM_BASH")
    if override:
        return override
    if sys.platform == "win32":
        for candidate in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ):
            if Path(candidate).exists():
                return candidate
    found = shutil.which("bash")
    if found is None:
        raise RuntimeError("bash não encontrado; defina WORKFLOW_SIM_BASH")
    return found


def load_job(workflow: Path, job_id: str) -> Mapping[str, Any]:
    """Carrega do YAML a definição do job solicitado."""
    data = yaml.safe_load(Path(workflow).read_text(encoding="utf-8"))
    return data["jobs"][job_id]


def _parse_outputs(path: Path) -> dict[str, str]:
    """Lê saídas simples e blocos multilinha escritos no arquivo GITHUB_OUTPUT."""
    outputs: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if "<<" in line.split("=", 1)[0]:
            key, delimiter = line.split("<<", 1)
            block: list[str] = []
            while index < len(lines) and lines[index] != delimiter:
                block.append(lines[index])
                index += 1
            index += 1
            outputs[key] = "\n".join(block)
        elif "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value
    return outputs


def run_job(
    workflow: Path,
    job_id: str,
    *,
    sandbox: Sandbox,
    context: Mapping[str, Any],
    workdir: Path,
) -> list[StepResult]:
    """Roda os passos `run:` do job, em ordem, parando no primeiro que falhar."""
    workflow_data = yaml.safe_load(Path(workflow).read_text(encoding="utf-8"))
    job = workflow_data["jobs"][job_id]
    ctx: dict[str, Any] = copy.deepcopy(dict(context))
    for key in ("github", "secrets", "needs", "env", "steps"):
        ctx.setdefault(key, {})
    github = ctx["github"]
    scenario_env = {
        key: render(_stringify(value), ctx) for key, value in ctx["env"].items()
    }
    workflow_env = {
        key: render(_stringify(value), ctx)
        for key, value in (workflow_data.get("env") or {}).items()
    }
    context_env = {**scenario_env, **workflow_env}
    ctx["env"] = context_env
    job_env = {
        key: render(_stringify(value), ctx)
        for key, value in (job.get("env") or {}).items()
    }
    bash = find_bash()
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="workflow_sim_"))
    results: list[StepResult] = []
    try:
        for step in job["steps"]:
            name = step.get("name")
            if "if" in step and not evaluate_condition(_stringify(step["if"]), ctx):
                results.append(StepResult(name, 0, "", "condição falsa"))
                continue
            if "uses" in step:
                results.append(
                    StepResult(name, 0, "", f"ação {step['uses']} (não simulada)")
                )
                continue
            ctx["env"] = {
                **context_env,
                **job_env,
                **{
                    key: render(_stringify(value), ctx)
                    for key, value in (step.get("env") or {}).items()
                },
            }
            script = scratch / "step.sh"
            script.write_text(render(step["run"], ctx), encoding="utf-8", newline="\n")
            output_file = scratch / "output"
            output_file.write_text("", encoding="utf-8")
            summary_file = scratch / "summary"
            summary_file.write_text("", encoding="utf-8")
            # Inherit only PATH for standard runner utilities. Credentials,
            # HOME/configuration and other host state must not enter a scenario.
            host_path = os.environ.get("PATH", os.defpath)
            runtime_env = {
                "PATH": f"{sandbox.bin}{os.pathsep}{host_path}",
                "HOME": str(scratch / "home"),
                "USERPROFILE": str(scratch / "home"),
                "TMP": str(scratch),
                "TEMP": str(scratch),
                "TMPDIR": str(scratch),
                "GH_CONFIG_DIR": str(scratch / "gh-config"),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "CI": "true",
                "GITHUB_SHA": _stringify(github.get("sha")),
                "GITHUB_REPOSITORY": _stringify(github.get("repository")),
                "GITHUB_REF": _stringify(github.get("ref")),
                "GITHUB_EVENT_NAME": _stringify(github.get("event_name")),
                "GITHUB_WORKSPACE": str(workdir),
                "GITHUB_OUTPUT": str(output_file),
                "GITHUB_STEP_SUMMARY": str(summary_file),
                "RUNNER_TEMP": str(scratch),
                **ctx["env"],
            }
            # A workflow may set PATH; keep the fake tool shims ahead of it.
            runtime_env["PATH"] = (
                f"{sandbox.bin}{os.pathsep}{runtime_env.get('PATH', host_path)}"
            )
            env = runtime_env
            flags = (
                ["--noprofile", "--norc", "-eo", "pipefail"]
                if step.get("shell") == "bash"
                else ["-e"]
            )
            process = subprocess.run(  # nosec B603
                [bash, *flags, str(script)],
                cwd=workdir / step.get("working-directory", "."),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if step.get("id"):
                ctx["steps"][step["id"]] = {"outputs": _parse_outputs(output_file)}
            results.append(
                StepResult(name, process.returncode, process.stdout + process.stderr)
            )
            if process.returncode != 0 and not step.get("continue-on-error"):
                break
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return results


# ── linha de comando ───────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    """Executa um cenário JSON pela CLI e exibe passos e chamadas simuladas."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        print("uso: workflow_sim.py WORKFLOW JOB CENARIO.json", file=sys.stderr)
        return 2
    workflow, job_id, scenario_path = Path(args[0]), args[1], Path(args[2])
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    tools = list(dict.fromkeys([*scenario.get("rules", {}), "gh"]))
    root = Path(tempfile.mkdtemp(prefix="workflow_sim_run_"))
    try:
        sandbox = Sandbox(root / "sandbox", tools=tools)
        for tool, rules in scenario.get("rules", {}).items():
            sandbox.set_rules(tool, [Rule(**rule) for rule in rules])
        workdir = root / "work"
        for relative, content in scenario.get("files", {}).items():
            target = workdir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        results = run_job(
            workflow,
            job_id,
            sandbox=sandbox,
            context=scenario.get("context", {}),
            workdir=workdir,
        )
        for number, result in enumerate(results, start=1):
            status = result.skipped or f"saída {result.exit_code}"
            print(f"== passo {number}: {result.name or '(sem nome)'} -> {status}")
            if result.output.strip():
                print(result.output.rstrip())
        print("== chamadas simuladas:")
        for call in sandbox.calls():
            print(f"   {call.tool} {' '.join(call.args)}")
        return 0 if all(r.exit_code == 0 for r in results) else 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

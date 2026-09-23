"""Limpa PRs e branches de fallback da ativação de uma linha de release."""

import subprocess  # nosec B404 - comandos fixos do checkout confiável
import sys
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class CleanupOperations:
    """Operações externas necessárias para limpar uma ativação obsoleta."""

    list_open_pull_requests: Callable[[], list[str]]
    close_pull_request: Callable[[str], None]
    pull_request_state: Callable[[str], str]
    branch_exists: Callable[[], bool]
    delete_branch: Callable[[], None]


def cleanup_activation_fallback(operations: CleanupOperations) -> None:
    """Limpa o fallback e ignora somente corridas já concluídas."""
    for pull_request in operations.list_open_pull_requests():
        try:
            operations.close_pull_request(pull_request)
        except Exception:
            if operations.pull_request_state(pull_request) == "OPEN":
                raise
    if operations.branch_exists():
        try:
            operations.delete_branch()
        except Exception:
            if operations.branch_exists():
                raise


def _run(*args: str, capture_output: bool = False) -> str:
    result = subprocess.run(  # nosec
        list(args), check=True, capture_output=capture_output, text=True
    )
    return result.stdout if capture_output else ""


def main() -> None:
    """Executa a limpeza usando GitHub CLI e Git no repositório informado."""
    if len(sys.argv) != 4:
        raise SystemExit("uso: cleanup_release_activation.py REPO BRANCH BASE")
    repository, branch, base = sys.argv[1:]

    def list_open() -> list[str]:
        output = _run(
            "gh",
            "pr",
            "list",
            "--repo",
            repository,
            "--head",
            branch,
            "--base",
            base,
            "--state",
            "open",
            "--json",
            "number",
            "--jq",
            ".[].number",
            capture_output=True,
        )
        return [value for value in output.splitlines() if value]

    def close(number: str) -> None:
        _run(
            "gh",
            "pr",
            "close",
            number,
            "--repo",
            repository,
            "--comment",
            "A ativação direta concluiu; esta PR de fallback ficou obsoleta.",
        )

    def state(number: str) -> str:
        return _run(
            "gh",
            "pr",
            "view",
            number,
            "--repo",
            repository,
            "--json",
            "state",
            "--jq",
            ".state",
            capture_output=True,
        ).strip()

    def exists() -> bool:
        return bool(
            _run(
                "git",
                "ls-remote",
                "--heads",
                "origin",
                f"refs/heads/{branch}",
                capture_output=True,
            ).strip()
        )

    def delete() -> None:
        _run("git", "push", "origin", "--delete", branch)

    cleanup_activation_fallback(
        CleanupOperations(list_open, close, state, exists, delete)
    )


if __name__ == "__main__":
    main()

"""Rastreamento limitado e isolado dos arquivos alterados em um turno."""

from __future__ import annotations

import difflib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypedDict, cast


class DiffHunk(TypedDict):
    """Representação serializável de um hunk de diff."""

    header: str
    lines: list[str]


class GitCommands(Protocol):
    def status(self, *args: str, **kwargs: object) -> str | bytes: ...

    def show(self, ref: str) -> str: ...


class RepoLike(Protocol):
    git: GitCommands
    working_tree_dir: str | None


@dataclass(frozen=True, slots=True)
class TurnFileChange:
    path: str
    status: str
    additions: int
    deletions: int
    hunks: list[DiffHunk]


@dataclass(frozen=True, slots=True)
class TurnWorkspaceSnapshot:
    run_id: str
    thread_id: str
    workspace_id: str
    repo_root: Path
    scope_root: Path
    initial: dict[str, bytes | None]
    initial_head: str | None


MAX_TRACKED_FILES = 256
MAX_FILE_BYTES = 512 * 1024
MAX_RETAINED_RUNS = 16
_active: dict[str, TurnWorkspaceSnapshot] = {}
_latest: dict[tuple[str, str], tuple[str, list[TurnFileChange]]] = {}
_results: dict[str, list[TurnFileChange]] = {}
_result_context: dict[str, tuple[str, str]] = {}


def _status_paths(repo: RepoLike, scope_root: Path, repo_root: Path) -> list[str]:
    """Lê caminhos com NUL para não quebrar nomes com espaços ou aspas."""
    try:
        raw = (
            repo.git.status("--porcelain=v1", "-z", "-uall", stdout_as_string=False)
            or b""
        )
    except Exception:
        return []
    if isinstance(raw, bytes):
        raw_text = raw.decode("utf-8", errors="surrogateescape")
    else:
        raw_text = raw
    paths: set[str] = set()
    records = raw_text.split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4:
            continue
        status = record[:2]
        path = record[3:]
        if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
            # With ``-z`` Git emits the destination first and the source as
            # the following NUL-delimited record. Only the destination is in
            # the current tree and should be shown in the turn card.
            if index < len(records):
                index += 1
        path = path.replace("\\", "/")
        candidate = (repo_root / path).resolve()
        try:
            candidate.relative_to(scope_root)
        except ValueError:
            continue
        paths.add(path)
    return sorted(paths)[:MAX_TRACKED_FILES]


def _read(repo_root: Path, path: str) -> bytes | None:
    candidate = (repo_root / path).resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError:
        return None
    try:
        if not candidate.is_file() or candidate.stat().st_size > MAX_FILE_BYTES:
            return None
        return candidate.read_bytes()
    except OSError:
        return None


def _head_read(repo: RepoLike, path: str, ref: str = "HEAD") -> bytes | None:
    try:
        return repo.git.show(f"{ref}:{path}").encode("utf-8", errors="replace")
    except Exception:
        return None


def _repo_and_scope(workspace_id: str) -> tuple[RepoLike, Path, Path] | None:
    from git import Repo  # type: ignore[import-not-found]

    from backend.workspace.workspace import workspace_registry

    workspace = workspace_registry.get(workspace_id)
    if workspace is None or not workspace.cwd:
        return None
    scope_root = Path(workspace.cwd).resolve()
    repo = Repo(str(scope_root), search_parent_directories=True)
    repo_root = Path(repo.working_tree_dir or scope_root).resolve()
    try:
        scope_root.relative_to(repo_root)
    except ValueError:
        return None
    return cast("RepoLike", repo), repo_root, scope_root


def capture(
    workspace_id: str | None, thread_id: str, run_id: str | None = None
) -> TurnWorkspaceSnapshot | None:
    """Captura o estado inicial do escopo do workspace para um run único."""
    if not workspace_id:
        return None
    try:
        resolved = _repo_and_scope(workspace_id)
        if resolved is None:
            return None
        repo, repo_root, scope_root = resolved
        initial = {
            path: _read(repo_root, path)
            for path in _status_paths(repo, scope_root, repo_root)
        }
        initial_head = repo.git.rev_parse("HEAD")
    except Exception:
        return None
    snapshot = TurnWorkspaceSnapshot(
        run_id=run_id or uuid.uuid4().hex,
        thread_id=thread_id,
        workspace_id=workspace_id,
        repo_root=repo_root,
        scope_root=scope_root,
        initial=initial,
        initial_head=initial_head,
    )
    _active[snapshot.run_id] = snapshot
    return snapshot


def _decode_lines(data: bytes | None) -> list[str] | None:
    if data is None:
        return []
    try:
        return data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None


def compute(snapshot: TurnWorkspaceSnapshot) -> list[TurnFileChange]:
    """Calcula alterações finais, sempre limitadas ao escopo capturado."""
    try:
        from git import Repo  # type: ignore[import-not-found]

        repo = cast(
            "RepoLike", Repo(str(snapshot.repo_root), search_parent_directories=True)
        )
    except Exception:
        return []

    current_paths = _status_paths(repo, snapshot.scope_root, snapshot.repo_root)
    committed_paths: list[str] = []
    if snapshot.initial_head:
        try:
            raw = repo.git.diff(
                "--name-only", f"{snapshot.initial_head}..HEAD", "--", str(snapshot.scope_root)
            )
            committed_paths = [p for p in raw.splitlines() if p]
        except Exception:
            committed_paths = []
    paths = sorted(set(snapshot.initial) | set(current_paths) | set(committed_paths))[:MAX_TRACKED_FILES]
    changes: list[TurnFileChange] = []
    for path in paths:
        before = snapshot.initial.get(
            path, _head_read(repo, path, snapshot.initial_head or "HEAD")
        )
        after = _read(snapshot.repo_root, path)
        if before == after:
            continue
        before_lines = _decode_lines(before)
        after_lines = _decode_lines(after)
        hunks: list[DiffHunk] = []
        additions = deletions = 0
        if before_lines is not None and after_lines is not None:
            raw = list(
                difflib.unified_diff(
                    before_lines,
                    after_lines,
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                    lineterm="",
                )
            )
            header = ""
            lines: list[str] = []
            for line in raw:
                if line.startswith("@@"):
                    if lines:
                        hunks.append({"header": header, "lines": lines})
                        lines = []
                    header = line
                elif line.startswith(("---", "+++")) and not header:
                    continue
                else:
                    lines.append(line)
                    if line.startswith("+") and not line.startswith("+++"):
                        additions += 1
                    elif line.startswith("-") and not line.startswith("---"):
                        deletions += 1
            if lines:
                hunks.append({"header": header, "lines": lines})
        else:
            additions = 1 if after is not None else 0
            deletions = 1 if before is not None else 0
        status = "A" if before is None else "D" if after is None else "M"
        changes.append(TurnFileChange(path, status, additions, deletions, hunks))
    return changes


def finalize(snapshot: TurnWorkspaceSnapshot | None) -> list[TurnFileChange]:
    """Finaliza somente o snapshot do run recebido."""
    if snapshot is None:
        return []
    changes = compute(snapshot)
    _results[snapshot.run_id] = changes
    _result_context[snapshot.run_id] = (snapshot.thread_id, snapshot.workspace_id)
    _active.pop(snapshot.run_id, None)
    _latest[(snapshot.thread_id, snapshot.workspace_id)] = (snapshot.run_id, changes)
    if len(_results) > MAX_RETAINED_RUNS:
        for old_run_id in list(_results)[:-MAX_RETAINED_RUNS]:
            _results.pop(old_run_id, None)
            _result_context.pop(old_run_id, None)
    return changes


def latest(
    thread_id: str, workspace_id: str, run_id: str | None = None
) -> tuple[str, str, list[TurnFileChange]]:
    """Retorna ``(run_id, status, files)`` do run solicitado ou mais recente."""
    if run_id:
        snapshot = _active.get(run_id)
        if (
            snapshot
            and snapshot.thread_id == thread_id
            and snapshot.workspace_id == workspace_id
        ):
            return run_id, "active", compute(snapshot)
        if _result_context.get(run_id) == (thread_id, workspace_id):
            return run_id, "finalized", _results[run_id]
        return run_id, "finalized", []
    latest_run, changes = _latest.get((thread_id, workspace_id), ("", []))
    if latest_run:
        return latest_run, "finalized", changes
    for active_run, snapshot in _active.items():
        if snapshot.thread_id == thread_id and snapshot.workspace_id == workspace_id:
            return active_run, "active", compute(snapshot)
    return "", "finalized", []


def encode(changes: list[TurnFileChange]) -> list[dict[str, object]]:
    return [
        {
            "path": change.path,
            "status": change.status,
            "additions": change.additions,
            "deletions": change.deletions,
            "hunks": change.hunks,
        }
        for change in changes
    ]

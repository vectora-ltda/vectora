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
    """Subset of GitPython commands required by the turn-file tracker."""

    def status(self, *args: str, **kwargs: object) -> str | bytes:
        """Return a porcelain working-tree status."""
        ...

    def show(self, ref: str) -> str:
        """Read a file from a Git revision."""
        ...

    def diff(self, *args: str, **kwargs: object) -> str | bytes:
        """Return a revision diff or a list of changed paths."""
        ...

    def cat_file(self, *args: str, **kwargs: object) -> str | bytes:
        """Check whether an object exists in a revision."""
        ...


class RepoLike(Protocol):
    """Minimal repository surface used by the tracker and its tests."""

    git: GitCommands
    working_tree_dir: str | None


@dataclass(frozen=True, slots=True)
class TurnFileChange:
    """One file changed between the start and end of a turn."""

    path: str
    status: str
    additions: int
    deletions: int
    hunks: list[DiffHunk]


@dataclass(frozen=True, slots=True)
class TurnWorkspaceSnapshot:
    """Immutable baseline used to calculate one run's file changes."""

    run_id: str
    thread_id: str
    workspace_id: str
    repo_root: Path
    scope_root: Path
    initial: dict[str, bytes | None]
    initial_head: str | None = None


MAX_TRACKED_FILES: int = 256
MAX_FILE_BYTES: int = 512 * 1024
MAX_RETAINED_RUNS: int = 16
_active: dict[str, TurnWorkspaceSnapshot] = {}
_active_by_context: dict[tuple[str, str], str] = {}
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


def _revision_paths(
    repo: RepoLike, revision_range: str, scope_root: Path, repo_root: Path
) -> list[str]:
    """Return paths changed by ``revision_range`` inside the workspace scope."""
    try:
        raw = (
            repo.git.diff(
                "--name-only",
                "-z",
                revision_range,
                "--",
                stdout_as_string=False,
            )
            or b""
        )
    except Exception:
        return []
    raw_text = (
        raw.decode("utf-8", errors="surrogateescape") if isinstance(raw, bytes) else raw
    )
    paths: set[str] = set()
    for raw_path in raw_text.split("\0"):
        if not raw_path:
            continue
        path = raw_path.replace("\\", "/")
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
    """Read a bounded file from ``ref`` without raising on missing paths."""
    try:
        raw = repo.git.show(f"{ref}:{path}")
        encoded = raw.encode("utf-8", errors="replace")
        return encoded if len(encoded) <= MAX_FILE_BYTES else None
    except Exception:
        return None


def _head_exists(repo: RepoLike, path: str, ref: str) -> bool:
    """Check whether ``path`` exists in a revision without reading its data."""
    try:
        repo.git.cat_file("-e", f"{ref}:{path}")
    except Exception:
        return False
    return True


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
        initial_head: str | None = None
        try:
            head = getattr(repo, "head", None)
            commit = getattr(head, "commit", None)
            commit_sha = getattr(commit, "hexsha", None)
            initial_head = str(commit_sha) if commit_sha else None
        except (AttributeError, ValueError):
            # A repository without its first commit has an unborn HEAD. The
            # working tree is still a valid baseline for turn tracking.
            initial_head = None
        initial = {
            path: _read(repo_root, path)
            for path in _status_paths(repo, scope_root, repo_root)
        }
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
    _active_by_context[(thread_id, workspace_id)] = snapshot.run_id
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
    committed_paths = (
        _revision_paths(
            repo,
            f"{snapshot.initial_head}..HEAD",
            snapshot.scope_root,
            snapshot.repo_root,
        )
        if snapshot.initial_head
        else []
    )
    paths = sorted(set(snapshot.initial) | set(current_paths) | set(committed_paths))[
        :MAX_TRACKED_FILES
    ]
    changes: list[TurnFileChange] = []
    for path in paths:
        before = (
            snapshot.initial[path]
            if path in snapshot.initial
            else _head_read(repo, path, snapshot.initial_head or "HEAD")
        )
        after = _read(snapshot.repo_root, path)
        before_ref = snapshot.initial_head or "HEAD"
        before_exists = path in snapshot.initial or (
            snapshot.initial_head is not None and _head_exists(repo, path, before_ref)
        )
        after_exists = (snapshot.repo_root / path).is_file()
        # Bounded reads intentionally return None for oversized files. Do not
        # turn an unreadable-but-present file into a false deletion.
        if before is None and after is None and before_exists and after_exists:
            continue
        if before is not None and after is None and after_exists:
            continue
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
                elif (
                    line
                    in {
                        f"--- a/{path}",
                        f"+++ b/{path}",
                    }
                    and not header
                ):
                    continue
                else:
                    lines.append(line)
                    if line.startswith("+"):
                        additions += 1
                    elif line.startswith("-"):
                        deletions += 1
            if lines:
                hunks.append({"header": header, "lines": lines})
        else:
            additions = 1 if after is not None else 0
            deletions = 1 if before is not None else 0
        status = "A" if not before_exists else "D" if not after_exists else "M"
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
    context = (snapshot.thread_id, snapshot.workspace_id)
    if _active_by_context.get(context) == snapshot.run_id:
        _active_by_context.pop(context, None)
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
    active_run = _active_by_context.get((thread_id, workspace_id))
    if active_run:
        snapshot = _active.get(active_run)
        if snapshot is not None:
            return active_run, "active", compute(snapshot)
    latest_run, changes = _latest.get((thread_id, workspace_id), ("", []))
    return latest_run, "finalized", changes


def encode(changes: list[TurnFileChange]) -> list[dict[str, object]]:
    """Serialize turn-file dataclasses for SSE and persistence boundaries."""
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

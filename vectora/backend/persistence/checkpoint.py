"""Checkpoints de workspace usados pelo rewind (não confundir com
checkpointing de estado de conversa — isso é o ``SessionStore`` nativo em
``backend/persistence/native/session_store.py``).

Expõe duas estratégias:

* **Git** (``create_git_checkpoint`` / ``restore_git_checkpoint`` /
  ``list_git_checkpoints``): snapshots gravados como commits soltos em
  ``refs/vectora/checkpoints/<thread_id>`` — não move HEAD nem o índice real.

* **Snapshot** (``create_snapshot_checkpoint`` / ``restore_snapshot_checkpoint`` /
  ``gc_snapshots``): fallback para workspaces sem ``.git`` — arquiva arquivos do
  workspace num tarball comprimido em ``~/.vectora/snapshots/<thread_id>/``.
  GC periódico mantém cap de tamanho/quantidade."""

import logging
import os
import shutil
import tarfile
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# GitPython roda Git.refresh() em `import git` e levanta ImportError se não
# achar o executável git no PATH — precisa disso antes do import abaixo.
os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")

import git

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Estratégia git de checkpoint de workspace (rewind — A.2)
# ---------------------------------------------------------------------------

# Autor fixo dos commits de checkpoint — nunca aparecem como autoria do
# usuário em `git log`/`git blame` da branch de trabalho (ficam isolados em
# `refs/vectora/checkpoints/*`, fora de `git branch -a`).
_CHECKPOINT_AUTHOR_NAME = "Vectora"
_CHECKPOINT_AUTHOR_EMAIL = "vectora@local"
_CHECKPOINT_REF_PREFIX = "refs/vectora/checkpoints/"


def checkpoint_ref(thread_id: str) -> str:
    """Nome da ref que encadeia os checkpoints de uma thread."""
    return f"{_CHECKPOINT_REF_PREFIX}{thread_id}"


def _checkpoint_env(index_file: str) -> dict[str, str]:
    return {
        "GIT_INDEX_FILE": index_file,
        "GIT_AUTHOR_NAME": _CHECKPOINT_AUTHOR_NAME,
        "GIT_AUTHOR_EMAIL": _CHECKPOINT_AUTHOR_EMAIL,
        "GIT_COMMITTER_NAME": _CHECKPOINT_AUTHOR_NAME,
        "GIT_COMMITTER_EMAIL": _CHECKPOINT_AUTHOR_EMAIL,
    }


def create_git_checkpoint(
    repo: git.Repo, thread_id: str, message: str
) -> dict[str, Any]:
    """Cria um commit-snapshot do worktree atual, sem tocar HEAD/índice/branch.

    Usa um índice git temporário (``GIT_INDEX_FILE`` apontando para um arquivo
    descartável): semeia-o a partir da árvore do HEAD via ``read-tree`` e roda
    ``add -A`` contra ele — captura staged + unstaged + untracked num único
    ``write-tree``, exatamente como ``git stash --include-untracked`` faz
    internamente, mas sem alterar `.git/index` nem o worktree do usuário.

    O commit resultante encadeia ao checkpoint anterior da thread (via
    ``refs/vectora/checkpoints/<thread_id>``) ou a HEAD, no primeiro
    checkpoint — formando um histórico linear navegável por
    ``git log refs/vectora/checkpoints/<thread_id>`` sem poluir
    ``git branch -a``.

    Retorna ``{"status": "ok", "sha": <commit>, "tree": <tree>}`` ou
    ``{"status": "error", "message": ...}`` em caso de falha do git.
    """
    try:
        head_sha: str | None = repo.head.commit.hexsha
    except (ValueError, git.GitCommandError, git.GitCommandNotFound):
        head_sha = None

    ref = checkpoint_ref(thread_id)
    try:
        parent_sha = repo.git.rev_parse(ref).strip()
    except (git.GitCommandError, git.GitCommandNotFound):
        parent_sha = head_sha

    try:
        with tempfile.TemporaryDirectory(prefix="vectora-checkpoint-") as tmp_dir:
            index_file = str(Path(tmp_dir) / "index")
            env = _checkpoint_env(index_file)
            if head_sha is not None:
                repo.git.read_tree(head_sha, env=env)
            repo.git.add("-A", env=env)
            tree_sha = repo.git.write_tree(env=env).strip()

            commit_args: list[str] = [tree_sha]
            if parent_sha is not None:
                commit_args += ["-p", parent_sha]
            commit_args += ["-m", message]
            commit_sha = repo.git.commit_tree(*commit_args, env=env).strip()

            repo.git.update_ref(ref, commit_sha)
    except (git.GitCommandError, git.GitCommandNotFound) as exc:
        return {"status": "error", "message": str(exc)}

    return {"status": "ok", "sha": commit_sha, "tree": tree_sha}


def restore_git_checkpoint(repo: git.Repo, sha: str) -> dict[str, Any]:
    """Restaura o worktree (e o índice) para o estado gravado em ``sha``.

    Equivalente a ``git restore --source=<sha> --worktree --staged -- .`` —
    sobrescreve arquivos rastreados e staged pelo conteúdo do snapshot, mas
    **não** remove arquivos criados depois do checkpoint (limitação conhecida
    de `git restore`; cobertura completa de "desfazer criação de arquivo"
    fica para A.3 — snapshot diferencial).
    """
    try:
        repo.git.restore("--source", sha, "--worktree", "--staged", "--", ".")
    except (git.GitCommandError, git.GitCommandNotFound) as exc:
        return {"status": "error", "message": str(exc)}
    return {"status": "ok", "sha": sha}


def list_git_checkpoints(repo: git.Repo, thread_id: str, n: int = 50) -> dict[str, Any]:
    """Lista os checkpoints da thread, do mais recente ao mais antigo."""
    ref = checkpoint_ref(thread_id)
    try:
        commits = list(repo.iter_commits(ref, max_count=n))
    except (git.GitCommandError, git.GitCommandNotFound):
        return {"status": "ok", "checkpoints": []}

    return {
        "status": "ok",
        "checkpoints": [
            {
                "sha": c.hexsha,
                "message": c.message.strip(),
                "date": c.authored_datetime.isoformat(),
            }
            for c in commits
        ],
    }


# ---------------------------------------------------------------------------
# Estratégia snapshot (fallback para workspaces sem .git — A.3)
# ---------------------------------------------------------------------------

# Diretórios ignorados ao criar um snapshot — build artifacts, caches, VCS.
_SNAPSHOT_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        ".nox",
        "dist",
        "build",
        ".next",
        ".nuxt",
        ".svelte-kit",
        "target",
        "venv",
        ".venv",
        "env",
        ".env",
        ".vectora",
    }
)

# Tamanho máximo de um arquivo individual incluído no snapshot (10 MiB).
_SNAPSHOT_MAX_FILE_BYTES = 10 * 1024 * 1024

# Cap de tamanho total por snapshot (50 MiB). Arquivos além do cap são omitidos.
_SNAPSHOT_MAX_TOTAL_BYTES = 50 * 1024 * 1024

# Margem reservada no cap total para o manifesto JSON, que é gravado depois
# dos arquivos e também conta no limite validado na restauração.
_SNAPSHOT_MANIFEST_RESERVE_BYTES = 1024 * 1024


def _iter_snapshot_files(cwd: Path) -> list[Path]:
    """Lista arquivos do workspace excluindo diretórios ignorados.

    Retorna caminhos absolutos ordenados para arquivamento determinístico.
    """
    result: list[Path] = []
    stack = [cwd]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in _SNAPSHOT_EXCLUDE_DIRS:
                    stack.append(entry)
            elif entry.is_file():
                result.append(entry)
    result.sort()
    return result


def create_snapshot_checkpoint(
    cwd: str,
    snapshot_dir: Path,
    thread_id: str,
    message: str,
) -> dict[str, Any]:
    """Cria um tarball comprimido do workspace como checkpoint de rewind.

    Usado como fallback quando o workspace não é um repositório git.  Arquiva
    todos os arquivos do diretório ``cwd`` (excluindo build artifacts / VCS /
    caches) num arquivo ``.tar.gz`` em ``snapshot_dir``.

    Arquivos individuais maiores que ``_SNAPSHOT_MAX_FILE_BYTES`` (10 MiB) e
    arquivos que fariam o tarball ultrapassar ``_SNAPSHOT_MAX_TOTAL_BYTES``
    (50 MiB) são omitidos silenciosamente.

    Retorna::

        {"status": "ok", "snapshot_path": "/abs/path/to/snap.tar.gz",
         "files_touched": ["rel/path1", ...]}
        {"status": "error", "message": "..."}
    """
    cwd_input = Path(cwd)
    if cwd_input.is_symlink() or not cwd_input.is_dir():
        return {"status": "error", "message": f"cwd não é um diretório: {cwd}"}
    cwd_path = cwd_input.resolve()

    if snapshot_dir.exists() and snapshot_dir.is_symlink():
        return {
            "status": "error",
            "message": "diretório de snapshots não pode ser symlink",
        }
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    if not snapshot_dir.is_dir():
        return {"status": "error", "message": "diretório de snapshots inválido"}

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    uid = str(uuid.uuid4())[:8]
    archive_name = f"{ts}-{uid}.tar.gz"
    archive_path = snapshot_dir / archive_name

    files = _iter_snapshot_files(cwd_path)
    files_touched: list[str] = []
    total_bytes = 0

    try:
        with tarfile.open(archive_path, "w:gz") as tar:
            for fpath in files:
                try:
                    size = fpath.stat().st_size
                except OSError:
                    continue
                if size > _SNAPSHOT_MAX_FILE_BYTES:
                    continue
                if (
                    total_bytes + size
                    > _SNAPSHOT_MAX_TOTAL_BYTES - _SNAPSHOT_MANIFEST_RESERVE_BYTES
                ):
                    continue
                arcname = str(fpath.relative_to(cwd_path))
                tar.add(str(fpath), arcname=arcname, recursive=False)
                files_touched.append(arcname)
                total_bytes += size

            # Manifesto interno com metadados do checkpoint.
            import io
            import json as _json

            manifest = _json.dumps(
                {
                    "thread_id": thread_id,
                    "message": message,
                    "created_at": datetime.now(UTC).isoformat(),
                    "files": files_touched,
                },
                ensure_ascii=False,
            ).encode()
            if total_bytes + len(manifest) > _SNAPSHOT_MAX_TOTAL_BYTES:
                raise ValueError("snapshot excede o limite total com o manifesto")
            minfo = tarfile.TarInfo(name=".vectora-snapshot-manifest.json")
            minfo.size = len(manifest)
            tar.addfile(minfo, io.BytesIO(manifest))

    except Exception as exc:
        archive_path.unlink(missing_ok=True)
        return {"status": "error", "message": str(exc)}

    return {
        "status": "ok",
        "snapshot_path": str(archive_path),
        "files_touched": files_touched,
    }


def restore_snapshot_checkpoint(snapshot_path: str, cwd: str) -> dict[str, Any]:
    """Restaura o workspace a partir de um tarball de snapshot.

    Extrai o arquivo sobre ``cwd``, sobrescrevendo os arquivos existentes.
    Não remove arquivos criados após o snapshot — cobertura completa de
    "desfazer criação de arquivo" fica para uma iteração futura (requer
    manifesto de arquivos excluídos).

    Retorna ``{"status": "ok"}`` ou ``{"status": "error", "message": ...}``.
    """
    snap = Path(snapshot_path)
    if snap.is_symlink() or not snap.is_file():
        return {
            "status": "error",
            "message": f"Snapshot não encontrado: {snapshot_path}",
        }
    cwd_input = Path(cwd)
    if cwd_input.is_symlink() or not cwd_input.is_dir():
        return {"status": "error", "message": f"cwd não é um diretório seguro: {cwd}"}
    cwd_path = cwd_input.resolve()
    total_bytes = 0
    try:
        with tarfile.open(str(snap), "r:gz") as tar:
            members = tar.getmembers()
            validated: list[tuple[tarfile.TarInfo, Path]] = []
            for member in members:
                # Check the archive name before touching the filesystem.  Do
                # not rely on ``extractall(filter=...)``: older Python
                # versions silently accept traversal and link entries.
                member_path = Path(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise ValueError(f"membro fora do workspace: {member.name}")
                if not member.isfile():
                    raise ValueError(f"tipo de membro não permitido: {member.name}")
                if member.size > _SNAPSHOT_MAX_FILE_BYTES:
                    raise ValueError(f"membro excede o limite: {member.name}")
                total_bytes += member.size
                if total_bytes > _SNAPSHOT_MAX_TOTAL_BYTES:
                    raise ValueError("snapshot excede o limite total")
                # The unresolved path is checked first: ``resolve()`` follows a
                # symlinked destination to its target, which would then pass
                # every check below and be overwritten in place.
                if (cwd_path / member_path).is_symlink():
                    raise ValueError(f"destino é symlink: {member.name}")
                destination = (cwd_path / member_path).resolve()
                try:
                    destination.relative_to(cwd_path)
                except ValueError as exc:
                    raise ValueError(
                        f"membro fora do workspace: {member.name}"
                    ) from exc
                # Check every existing path component before any write.  A
                # symlink nested below the workspace must never redirect a
                # restore outside the selected root.
                cursor = cwd_path
                for component in member_path.parts[:-1]:
                    cursor = cursor / component
                    if cursor.is_symlink():
                        raise ValueError(f"caminho pai é symlink: {member.name}")
                validated.append((member, destination))

            # Validate the complete archive before touching the workspace.
            # Stage files in a private directory, then publish each file only
            # after all reads have succeeded; a malformed later member cannot
            # leave an earlier member partially written.
            staging = Path(tempfile.mkdtemp(prefix=".vectora-restore-", dir=cwd_path))
            try:
                staged: list[tuple[Path, Path]] = []
                for member, destination in validated:
                    source = tar.extractfile(member)
                    if source is None:
                        raise ValueError(f"membro sem conteúdo: {member.name}")
                    staged_path = staging / member.name
                    staged_path.parent.mkdir(parents=True, exist_ok=True)
                    with source, staged_path.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    # Keep the executable bit recorded in the snapshot. Only
                    # permission bits are applied: setuid/setgid/sticky stay
                    # out of a restored workspace.
                    staged_path.chmod(member.mode & 0o777)
                    staged.append((staged_path, destination))
                for staged_path, destination in staged:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    staged_path.replace(destination)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    return {"status": "ok", "snapshot_path": snapshot_path}


def gc_snapshots(
    snapshot_dir: Path,
    max_snapshots: int = 30,
    max_bytes: int = 500 * 1024 * 1024,
) -> int:
    """Remove snapshots antigos que excedam ``max_snapshots`` ou ``max_bytes``.

    Ordena por mtime ascendente (mais antigo primeiro) e deleta até que ambos
    os limites sejam satisfeitos.  Retorna o número de arquivos removidos.
    """
    if not snapshot_dir.is_dir():
        return 0

    archives = sorted(
        (path for path in snapshot_dir.glob("*.tar.gz") if not path.is_symlink()),
        key=lambda p: p.stat().st_mtime,
    )

    total = sum(p.stat().st_size for p in archives)
    removed = 0

    while archives and (len(archives) > max_snapshots or total > max_bytes):
        oldest = archives.pop(0)
        size = oldest.stat().st_size
        try:
            oldest.unlink()
            total -= size
            removed += 1
        except OSError:
            pass  # já deletado por processo concorrente — ignora

    return removed

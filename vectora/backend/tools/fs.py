"""Filesystem tools: leitura, escrita, edição de arquivos, grep, listagem, terminal e artifacts.

Tools nativas (``@vtool``) — chamadas como função async direta com
``ctx: ToolContext``. Toda I/O bloqueante de arquivo (``file_read``/
``file_write``/``file_edit``/``grep``/``list_dir``) roda via
``asyncio.to_thread``, nunca bloqueando o event loop.
"""

from __future__ import annotations

import asyncio
import ctypes
import errno
import json
import logging
import os
import platform
import re
import shlex
import shutil
import stat
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.sandbox.policy import SandboxPolicy, parse_policy
from backend.sandbox.workspace_jail import WorkerSpawnError, jail_manager
from backend.services.ignore import is_ignored as _is_ignored
from backend.services.ignore import iter_files as _iter_files
from backend.services.ignore import load_ignore_spec as _load_ignore_spec
from backend.services.ignore import walk_files as _walk_files
from backend.services.security import (
    is_safe_regex_pattern,
    is_safe_shell_command,
    is_sensitive_path,
    resolve_within_workspace,
)
from backend.services.terminal_stream import emit_terminal_line
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool
from backend.vtypes.documents import VALID_ARTIFACT_TYPES

logger = logging.getLogger(__name__)


def _delete_confined(path: Path, root: Path) -> None:
    """Revalida o caminho imediatamente antes da remoção.

    A operação rejeita symlinks e qualquer troca do diretório pai observada
    entre a validação inicial e o I/O destrutivo. Isso reduz a janela de
    substituição do workspace por um link durante a chamada assíncrona.
    """
    root_real = root.resolve(strict=True)
    candidate = path.absolute()
    _assert_no_symlink_components(candidate, root_real)
    if candidate.is_symlink():
        raise ValueError("symlink não pode ser removido por esta tool")
    try:
        candidate.resolve(strict=True).relative_to(root_real)
    except ValueError as exc:
        raise ValueError("caminho fora do workspace") from exc
    relative = candidate.relative_to(root_real)
    if _supports_descriptor_operations():
        parent_fd, name = _open_parent_descriptor(root_real, relative)
        try:
            if stat.S_ISDIR(
                os.stat(name, dir_fd=parent_fd, follow_symlinks=False).st_mode
            ):
                shutil.rmtree(name, dir_fd=parent_fd)
            else:
                os.unlink(name, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        return
    if candidate.is_dir():
        shutil.rmtree(candidate)
    else:
        candidate.unlink()


def _move_no_replace(source: Path, target: Path) -> None:
    """Move sem substituir um destino criado por outra tarefa.

    Arquivos usam hard-link + unlink, operação atômica de criação sem
    substituição. Diretórios usam rename, que preserva a semântica nativa do
    sistema e falha quando o destino já existe no Windows.
    """
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    if source.is_dir():
        _rename_no_replace(source, target)
        return
    os.link(source, target)
    try:
        source.unlink()
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _rename_no_replace(source: Path, target: Path) -> None:
    """Rename a directory without replacing a concurrently-created target."""
    if os.name == "nt":
        # Windows ``MoveFileEx`` semantics used by ``os.rename`` reject an
        # existing destination, including one created after our prior check.
        source.rename(target)
        return
    if sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise OSError(errno.ENOTSUP, "renameat2 não está disponível")
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            -100,
            os.fsencode(source),
            -100,
            os.fsencode(target),
            1,  # RENAME_NOREPLACE
        )
        if result != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(target))
        return
    raise OSError(
        errno.ENOTSUP, "movimentação de diretório sem substituição não suportada"
    )


def _supports_descriptor_operations() -> bool:
    """Return whether this platform supports descriptor-relative safe I/O."""
    return (
        os.name != "nt"
        and getattr(os, "O_DIRECTORY", None) is not None
        and getattr(os, "O_NOFOLLOW", None) is not None
        and os.open in os.supports_dir_fd
    )


def _open_parent_descriptor(root: Path, relative: Path) -> tuple[int, str]:
    """Open the validated parent chain without following symlinks."""
    if not relative.parts:
        raise ValueError("a raiz do workspace não pode ser alterada")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(root, flags)
    try:
        for component in relative.parts[:-1]:
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
    except Exception:
        os.close(fd)
        raise
    return fd, relative.parts[-1]


def _mkdir_confined(path: Path, root: Path) -> None:
    """Create a directory tree while keeping each ancestor descriptor-bound."""
    root_real = root.resolve(strict=True)
    relative = path.absolute().relative_to(root_real)
    if not relative.parts:
        return
    if not _supports_descriptor_operations():
        path.mkdir(parents=True, exist_ok=True)
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(root_real, flags)
    try:
        for component in relative.parts:
            try:
                os.mkdir(component, dir_fd=fd)
            except FileExistsError:
                if not stat.S_ISDIR(
                    os.stat(component, dir_fd=fd, follow_symlinks=False).st_mode
                ):
                    raise
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
    finally:
        os.close(fd)


def _move_confined(source: Path, target: Path, root: Path) -> None:
    """Move using validated parent descriptors where the OS supports it."""
    root_real = root.resolve(strict=True)
    source_rel = source.absolute().relative_to(root_real)
    target_rel = target.absolute().relative_to(root_real)
    if not _supports_descriptor_operations():
        _move_no_replace(source, target)
        return
    source_fd, source_name = _open_parent_descriptor(root_real, source_rel)
    target_fd, target_name = _open_parent_descriptor(root_real, target_rel)
    try:
        try:
            os.stat(target_name, dir_fd=target_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(target)
        if stat.S_ISDIR(
            os.stat(source_name, dir_fd=source_fd, follow_symlinks=False).st_mode
        ):
            if sys.platform != "linux":
                raise OSError(
                    errno.ENOTSUP,
                    "movimentação segura de diretório não suportada nesta plataforma",
                )
            libc = ctypes.CDLL(None, use_errno=True)
            renameat2 = getattr(libc, "renameat2", None)
            if renameat2 is None:
                raise OSError(errno.ENOTSUP, "renameat2 não está disponível")
            renameat2.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            renameat2.restype = ctypes.c_int
            result = renameat2(
                source_fd,
                os.fsencode(source_name),
                target_fd,
                os.fsencode(target_name),
                1,
            )
            if result != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error), str(target))
        else:
            os.link(
                source_name,
                target_name,
                src_dir_fd=source_fd,
                dst_dir_fd=target_fd,
                follow_symlinks=False,
            )
            os.unlink(source_name, dir_fd=source_fd)
    finally:
        os.close(source_fd)
        os.close(target_fd)


def _assert_no_symlink_components(path: Path, root: Path) -> None:
    """Reject symlinked path components before a mutating filesystem call."""
    root_real = root.resolve(strict=True)
    candidate = path.absolute()
    try:
        relative = candidate.relative_to(root_real)
    except ValueError as exc:
        raise ValueError("caminho fora do workspace") from exc
    current = root_real
    for component in relative.parts:
        current /= component
        if current.is_symlink():
            raise ValueError("symlink não pode redirecionar a operação")


# ---------------------------------------------------------------------------
# Confinação ao workspace ativo (scope guard rails)
# ---------------------------------------------------------------------------


def _active_workspace(ctx: ToolContext) -> Any:
    """Resolve o Workspace ativo a partir do ctx (workspace_id).

    Sem ``ctx.workspace_id`` (ou apontando pra um id que não existe mais no
    registry), tenta o workspace marcado como ativo do usuário antes de
    cair no último recurso (``get_or_create()`` sem ``cwd``, que resolve
    pelo diretório de trabalho do PROCESSO do backend — quase nunca o que o
    usuário está vendo selecionado na UI). Sem esse fallback intermediário,
    uma tool call sem workspace_id propagado escreve silenciosamente no
    workspace errado."""
    from backend.workspace.workspace import workspace_registry

    if ctx.workspace_id:
        ws = workspace_registry.get(ctx.workspace_id)
        if ws is not None:
            return ws
    active = workspace_registry.get_active(ctx.user_id)
    if active is not None:
        return active
    return workspace_registry.get_or_create()


def _workspace_root(ctx: ToolContext) -> tuple[Path, Any]:
    """Retorna (root, workspace) do workspace ativo."""
    ws = _active_workspace(ctx)
    return Path(ws.cwd), ws


def _confine(path: str, ctx: ToolContext) -> tuple[Path | None, str]:
    """Resolve ``path`` dentro do workspace ativo.

    Retorna (resolved_path, "") em sucesso ou (None, error_message) se o path
    escapar do workspace.
    """
    root, _ws = _workspace_root(ctx)
    resolved = resolve_within_workspace(path, root)
    if resolved is None:
        return None, (
            f"Error: Path '{path}' fora do workspace '{root}'. "
            "O Vectora só pode acessar arquivos dentro da pasta confiável."
        )
    if is_sensitive_path(resolved):
        return None, (
            f"Error: Path '{path}' é um arquivo/diretório de credencial "
            "sensível (chave SSH, credencial cloud, .env, .pem) — bloqueado "
            "independentemente do sandbox estar ativo."
        )
    return resolved, ""


def _require_trust(ctx: ToolContext) -> str:
    """Retorna mensagem de erro se o workspace ativo não for confiável, senão ""."""
    _, ws = _workspace_root(ctx)
    if not getattr(ws, "trusted", False):
        return (
            f"Error: Workspace '{ws.name}' não é confiável. Confirme a confiança "
            "na pasta antes de executar ações de escrita ou terminal."
        )
    return ""


def _require_local(ctx: ToolContext) -> str:
    """Rejeita workspaces remotos para tools de filesystem que fazem I/O
    direto via ``Path()`` (sem transporte remoto)."""
    _, ws = _workspace_root(ctx)
    transport = str(getattr(ws, "transport", "local"))
    if transport != "local":
        return (
            f"Error: Esta tool ainda só funciona em workspaces locais. "
            f"Workspace '{ws.name}' usa transport={transport!r}. "
            "Use a tool `terminal` para executar comandos remoto."
        )
    return ""


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="filesystem",
        destructive=False,
        icon="file-text",
    )
)
async def file_read(file_path: str, ctx: ToolContext) -> str:
    """Lê conteúdo completo de um arquivo de texto.

    Args:
        file_path: Caminho relativo ou absoluto do arquivo

    Returns:
        Conteúdo do arquivo como string
    """
    if remote_err := _require_local(ctx):
        return remote_err
    resolved, err = _confine(file_path, ctx)
    if resolved is None:
        logger.warning("file_read blocked by scope check", extra={"path": file_path})
        return err

    try:
        content = await asyncio.to_thread(resolved.read_text, encoding="utf-8")
        logger.info(
            "file_read completed", extra={"path": file_path, "size": len(content)}
        )
        return content
    except FileNotFoundError:
        return f"Error: File '{file_path}' not found"
    except Exception:
        logger.exception("file_read failed", extra={"path": file_path})
        return "Error reading file. Check logs."


def _build_hook_argv(cmd_template: str, path: Path) -> list[str]:
    """Tokeniza o template (``shlex``, sintaxe de shell só pra separar
    argumentos) e substitui ``{file}`` dentro de cada token já resolvido —
    nunca concatena string pra reinterpretar como shell. Um nome de arquivo
    contendo `; rm -rf /` vira um argumento literal, não um comando novo."""
    tokens = shlex.split(cmd_template)
    return [tok.replace("{file}", str(path)) for tok in tokens]


async def _run_hooks_and_autocommit(path: Path, root: Path, ws: Any) -> str:
    """Roda hooks ``post_file_write`` + auto-commit opcional (``vectora.toml``).

    Nunca propaga falha pra tool que chamou — retorna uma nota (string
    vazia se nada a reportar) pra anexar à própria resposta. Pausa para
    aprovação quando hooks estão configurados mas ainda não aprovados
    (nunca executados sem aprovação explícita — diferente de `trusted`,
    que só cobre leitura/escrita de arquivo, não comando de shell
    arbitrário vindo do repositório).
    """
    try:
        from backend.workspace.workspace_config import load_workspace_config

        cfg = await asyncio.to_thread(load_workspace_config, root)
        if cfg is None or (not cfg.hooks.post_file_write and not cfg.agent.auto_commit):
            return ""

        note = ""
        if cfg.hooks.post_file_write:
            if not getattr(ws, "hooks_approved", False):
                note = (
                    "Nota: este workspace tem hooks [hooks].post_file_write "
                    "configurados em vectora.toml, mas eles ainda não foram "
                    "aprovados — não foram executados. Aprove em Configurações > "
                    "Workspace para habilitar lint/format automático pós-escrita."
                )
            else:
                workspace_id = str(getattr(ws, "id", root))
                policy = parse_policy(root / "vectora.toml")
                for cmd_template in cfg.hooks.post_file_write:
                    argv = _build_hook_argv(cmd_template, path)
                    if policy.enabled:
                        worker = await jail_manager.get_or_spawn(
                            workspace_id, str(root), policy
                        )
                        resp = await worker.request("exec", command=argv)
                        exit_code = resp.get("exit_code")
                    else:
                        proc = await asyncio.create_subprocess_exec(
                            *argv,
                            cwd=str(root),
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await proc.wait()
                        exit_code = proc.returncode
                    logger.info(
                        "post_file_write_hook_executed",
                        extra={"command": cmd_template, "exit_code": exit_code},
                    )

        if cfg.agent.auto_commit:
            rel = path.relative_to(root) if path.is_relative_to(root) else path
            add = await asyncio.create_subprocess_exec(
                "git",
                "add",
                str(path),
                cwd=str(root),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await add.wait()
            commit = await asyncio.create_subprocess_exec(
                "git",
                "commit",
                "-m",
                f"auto: update {rel}",
                cwd=str(root),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await commit.wait()
            logger.info("auto_commit_executed", extra={"path": str(rel)})

        return note
    except Exception:
        logger.warning(
            "post_write_hooks_failed", extra={"path": str(path)}, exc_info=True
        )
        return ""


@vtool(
    extras=ToolExtras(
        render_hint="diff",
        category="filesystem",
        destructive=True,
        icon="file-edit",
        invalidates=["files", "diff"],
    )
)
async def file_edit(
    file_path: str,
    old_text: str,
    new_text: str,
    ctx: ToolContext,
    replace_all: bool = False,
) -> str:
    """Edita arquivo substituindo texto.

    Args:
        file_path: Caminho do arquivo
        old_text: Texto a encontrar (use "" para criar arquivo se não existir)
        new_text: Texto de substituição
        replace_all: Se True, substitui todas as ocorrências; padrão substitui apenas a 1ª

    Returns:
        Confirmação da edição
    """
    if remote_err := _require_local(ctx):
        return remote_err
    trust_err = _require_trust(ctx)
    if trust_err:
        return trust_err

    resolved, err = _confine(file_path, ctx)
    if resolved is None:
        logger.warning("file_edit blocked by scope check", extra={"path": file_path})
        return err

    root, ws = _workspace_root(ctx)
    policy = parse_policy(root / "vectora.toml")
    workspace_id = str(getattr(ws, "id", root))

    try:
        path = resolved
        worker = None
        if policy.enabled:
            worker = await jail_manager.get_or_spawn(workspace_id, str(root), policy)
            read_resp = await worker.request("read_file", path=str(path))
            if "error" in read_resp:
                if old_text != "":
                    return "Error: Text not found in file"
                write_resp = await worker.request(
                    "write_file", path=str(path), content=new_text
                )
                if "error" in write_resp:
                    return f"Error: {write_resp['error']}"
                logger.info("file_edit created new file", extra={"path": file_path})
                note = await _run_hooks_and_autocommit(path, root, ws)
                return f"[OK] File created: {file_path}" + (f"\n{note}" if note else "")
            content = read_resp["content"]
        else:
            # Cria arquivo novo quando old_text="" e arquivo não existe
            if old_text == "" and not path.exists():
                await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
                await asyncio.to_thread(path.write_text, new_text, encoding="utf-8")
                logger.info("file_edit created new file", extra={"path": file_path})
                note = await _run_hooks_and_autocommit(path, root, ws)
                return f"[OK] File created: {file_path}" + (f"\n{note}" if note else "")
            content = await asyncio.to_thread(path.read_text, encoding="utf-8")

        if old_text and old_text not in content:
            return "Error: Text not found in file"

        new_content = (
            content.replace(old_text, new_text)
            if replace_all
            else content.replace(old_text, new_text, 1)
        )

        if policy.enabled and worker is not None:
            write_resp = await worker.request(
                "write_file", path=str(path), content=new_content
            )
            if "error" in write_resp:
                return f"Error: {write_resp['error']}"
        else:
            await asyncio.to_thread(path.write_text, new_content, encoding="utf-8")

        count = content.count(old_text) if replace_all else 1
        logger.info(
            "file_edit completed",
            extra={"path": file_path, "occurrences": count, "replace_all": replace_all},
        )
        note = await _run_hooks_and_autocommit(path, root, ws)
        return (
            f"[OK] File edited successfully ({count} occurrence{'s' if count != 1 else ''} replaced)"
            + (f"\n{note}" if note else "")
        )
    except WorkerSpawnError as exc:
        logger.warning("file_edit sandbox spawn failed", extra={"path": file_path})
        return f"Error: {exc}"
    except Exception:
        logger.exception("file_edit failed", extra={"path": file_path})
        return "Error editing file. Check logs."


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="filesystem",
        destructive=True,
        icon="file-plus",
        invalidates=["files", "diff"],
    )
)
async def file_write(file_path: str, content: str, ctx: ToolContext) -> str:
    """Cria ou sobrescreve completamente um arquivo com o conteúdo fornecido.

    Use para criar novos arquivos ou substituir o conteúdo completo de um existente.
    Para edições cirúrgicas (substituir trechos), prefira file_edit.

    Args:
        file_path: Caminho do arquivo (absoluto ou relativo)
        content: Conteúdo completo a escrever no arquivo

    Returns:
        Confirmação com caminho e tamanho em bytes
    """
    if remote_err := _require_local(ctx):
        return remote_err
    trust_err = _require_trust(ctx)
    if trust_err:
        return trust_err

    resolved, err = _confine(file_path, ctx)
    if resolved is None:
        logger.warning("file_write blocked by scope check", extra={"path": file_path})
        return err

    root, ws = _workspace_root(ctx)
    policy = parse_policy(root / "vectora.toml")

    try:
        path = resolved
        if policy.enabled:
            worker = await jail_manager.get_or_spawn(
                str(getattr(ws, "id", root)), str(root), policy
            )
            resp = await worker.request("write_file", path=str(path), content=content)
            if "error" in resp:
                return f"Error: {resp['error']}"
            size = len(content.encode("utf-8"))
        else:
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_text, content, encoding="utf-8")
            size = path.stat().st_size

        logger.info(
            "file_write completed", extra={"path": file_path, "size_bytes": size}
        )
        note = await _run_hooks_and_autocommit(path, root, ws)
        return f"[OK] File written: {file_path} ({size} bytes)" + (
            f"\n{note}" if note else ""
        )
    except WorkerSpawnError as exc:
        logger.warning("file_write sandbox spawn failed", extra={"path": file_path})
        return f"Error: {exc}"
    except Exception:
        logger.exception("file_write failed", extra={"path": file_path})
        return "Error writing file. Check logs."


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="filesystem",
        destructive=True,
        icon="folder-plus",
        invalidates=["files"],
    )
)
async def file_create_dir(path: str, ctx: ToolContext) -> str:
    """Cria um diretório dentro do workspace confiável."""
    if remote_err := _require_local(ctx):
        return remote_err
    if trust_err := _require_trust(ctx):
        return trust_err
    resolved, err = _confine(path, ctx)
    if resolved is None:
        return err
    try:
        root, _ = _workspace_root(ctx)
        await asyncio.to_thread(_assert_no_symlink_components, resolved, root)
        await asyncio.to_thread(_mkdir_confined, resolved, root)
        return f"[OK] Diretório criado: {path}"
    except (OSError, ValueError) as exc:
        return f"Error criando diretório: {exc}"


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="filesystem",
        destructive=True,
        icon="trash-2",
        invalidates=["files", "diff"],
    )
)
async def file_delete(path: str, ctx: ToolContext, permanent: bool = False) -> str:
    """Remove um arquivo ou diretório; usa lixeira por padrão."""
    if remote_err := _require_local(ctx):
        return remote_err
    if trust_err := _require_trust(ctx):
        return trust_err
    resolved, err = _confine(path, ctx)
    if resolved is None:
        return err
    if not resolved.exists():
        return f"Error: caminho não encontrado: {path}"
    try:
        if permanent:
            root, _ = _workspace_root(ctx)
            await asyncio.to_thread(_delete_confined, resolved, root)
        else:
            import send2trash

            await asyncio.to_thread(send2trash.send2trash, str(resolved))
        return f"[OK] Caminho removido: {path}"
    except OSError as exc:
        return f"Error removendo caminho: {exc}"


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="filesystem",
        destructive=True,
        icon="move",
        invalidates=["files", "diff"],
    )
)
async def file_move(from_path: str, to_path: str, ctx: ToolContext) -> str:
    """Move ou renomeia um caminho sem permitir sair do workspace."""
    if remote_err := _require_local(ctx):
        return remote_err
    if trust_err := _require_trust(ctx):
        return trust_err
    source, source_err = _confine(from_path, ctx)
    target, target_err = _confine(to_path, ctx)
    if source is None:
        return source_err
    if target is None:
        return target_err
    if not source.exists():
        return f"Error: origem não encontrada: {from_path}"
    if target.exists():
        return f"Error: destino já existe: {to_path}"
    try:
        root, _ = _workspace_root(ctx)
        await asyncio.to_thread(_assert_no_symlink_components, source, root)
        await asyncio.to_thread(_assert_no_symlink_components, target, root)
        await asyncio.to_thread(_mkdir_confined, target.parent, root)
        await asyncio.to_thread(_assert_no_symlink_components, target.parent, root)
        await asyncio.to_thread(_move_confined, source, target, root)
        return f"[OK] Movido: {from_path} -> {to_path}"
    except (OSError, ValueError) as exc:
        return f"Error movendo caminho: {exc}"


@vtool(
    extras=ToolExtras(
        render_hint="table",
        category="filesystem",
        destructive=False,
        icon="search",
    )
)
async def file_search(query: str, ctx: ToolContext, path: str = ".") -> str:
    """Busca texto no workspace, com o mesmo contrato do Files Workbench."""
    if not query.strip():
        return "Error: query é obrigatório"
    return await grep(re.escape(query), ctx, path)


def _grep_sync(pattern: str, search_path: Path) -> list[str]:
    results: list[str] = []
    base_dir = search_path if search_path.is_dir() else search_path.parent
    spec = _load_ignore_spec(base_dir)

    # iter_files poda node_modules/.venv/etc. durante o walk — rglob("*")
    # puro varria essas árvores inteiras antes de filtrar.
    files = (
        [search_path]
        if search_path.is_file()
        else _iter_files(search_path, "**/*", spec)
    )

    for file_path in files:
        if not file_path.is_file():
            continue
        if _is_ignored(file_path, base_dir, spec):
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            for line_num, line in enumerate(content.split("\n"), 1):
                if re.search(pattern, line):
                    results.append(f"{file_path}:{line_num}: {line}")
        except Exception:
            pass
    return results


@vtool(
    extras=ToolExtras(
        render_hint="table",
        category="filesystem",
        destructive=False,
        icon="search",
    )
)
async def grep(pattern: str, ctx: ToolContext, path: str = ".") -> str:
    """Busca padrão em arquivos usando regex.

    Args:
        pattern: Padrão regex para buscar
        path: Caminho da pasta ou arquivo

    Returns:
        Linhas que correspondem ao padrão (arquivo:linha: conteúdo)
    """
    if remote_err := _require_local(ctx):
        return remote_err
    if not is_safe_regex_pattern(pattern):
        return "Error: Invalid or unsafe regex pattern"

    resolved, err = _confine(path, ctx)
    if resolved is None:
        return err

    try:
        results = await asyncio.to_thread(_grep_sync, pattern, resolved)
        logger.info(
            "grep completed",
            extra={"pattern": pattern, "path": path, "matches": len(results)},
        )
        return "\n".join(results[:100]) if results else "No matches found"
    except Exception:
        logger.exception("grep failed", extra={"pattern": pattern, "path": path})
        return "Error during grep. Check logs."


def _list_dir_sync(dir_path: Path, recursive: bool) -> list[str]:
    spec = _load_ignore_spec(dir_path)
    items: list[str] = []
    if recursive:
        # walk_files poda node_modules/.venv/etc. durante o walk;
        # include_dirs=True mantém os diretórios não podados na listagem.
        entries, _ = _walk_files(dir_path, "**/*", spec, include_dirs=True)
        for item in entries:
            rel_path = item.relative_to(dir_path)
            prefix = "[DIR]" if item.is_dir() else "[FILE]"
            items.append(f"{prefix} {rel_path}")
    else:
        for item in sorted(dir_path.iterdir()):
            if _is_ignored(item, dir_path, spec):
                continue
            prefix = "[DIR]" if item.is_dir() else "[FILE]"
            items.append(f"{prefix} {item.name}")
    return items


@vtool(
    extras=ToolExtras(
        render_hint="table",
        category="filesystem",
        destructive=False,
        icon="folder",
    )
)
async def list_dir(
    ctx: ToolContext, path: str = ".", *, recursive: bool = False
) -> str:
    """Lista arquivos em um diretório.

    Args:
        path: Caminho do diretório
        recursive: Se True, lista recursivamente

    Returns:
        Lista de arquivos e pastas com prefixo [DIR] ou [FILE]
    """
    if remote_err := _require_local(ctx):
        return remote_err
    resolved, err = _confine(path, ctx)
    if resolved is None:
        return err

    try:
        dir_path = resolved

        if not dir_path.exists():
            return f"Error: Directory '{path}' not found"
        if not dir_path.is_dir():
            return f"Error: '{path}' is not a directory"

        items = await asyncio.to_thread(_list_dir_sync, dir_path, recursive)

        logger.info(
            "list_dir completed",
            extra={"path": path, "recursive": recursive, "count": len(items)},
        )
        return "\n".join(items[:500]) if items else "(empty directory)"
    except Exception:
        logger.exception("list_dir failed", extra={"path": path})
        return "Error listing directory. Check logs."


#: Comandos em execução aguardando o próximo turno — chave é ``thread_id``.
#: Permite responder a um prompt interativo (ex.: "continuar? [y/N]") sem
#: matar o processo: a tool devolve o controle ao agente quando fica idle
#: por muito tempo com o processo ainda vivo, e uma chamada seguinte com
#: ``stdin_input`` retoma a MESMA sessão em vez de spawnar um comando novo.
_pending_terminal: dict[str, dict[str, Any]] = {}

_IDLE_TIMEOUT = 6.0
"""Sem output novo por esse tempo + processo vivo → provavelmente esperando
input; devolve o controle ao agente em vez de continuar bloqueado."""

_HARD_TIMEOUT = 60.0
"""Teto absoluto — depois disso o processo é morto de verdade."""


async def _drain_terminal_output(
    thread_id: str,
    proc: asyncio.subprocess.Process,
    output_lines: list[str],
    start: float,
    read_state: dict[str, Any] | None = None,
) -> str | None:
    """Aguarda output novo com idle-detection. None = processo terminou normalmente.

    Enquanto o processo está vivo, corre duas tasks de leitura (stdout/stderr)
    em paralelo com um watchdog que mede o tempo desde o último output. Se o
    processo ficar ``_IDLE_TIMEOUT`` segundos sem produzir nada (mas ainda
    vivo), assume que está esperando input — registra em ``_pending_terminal``
    (incluindo ``read_state``, para a próxima chamada REUSAR as mesmas tasks de
    leitura em vez de abrir um segundo leitor concorrente no mesmo stream) e
    devolve uma string com o output parcial + instrução.
    """
    if read_state is None:
        last_activity = [time.monotonic()]

        async def _stream(stream: asyncio.StreamReader | None) -> None:
            if stream is None:
                return
            while True:
                raw = await stream.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                output_lines.append(line)
                emit_terminal_line(line)
                last_activity[0] = time.monotonic()

        stdout_task = asyncio.ensure_future(_stream(proc.stdout))
        stderr_task = asyncio.ensure_future(_stream(proc.stderr))
        read_state = {
            "both": asyncio.gather(stdout_task, stderr_task),
            "last_activity": last_activity,
        }

    both = read_state["both"]
    last_activity = read_state["last_activity"]

    while True:
        try:
            await asyncio.wait_for(asyncio.shield(both), timeout=0.3)
            break  # ambos os streams fecharam — processo terminou de escrever
        except TimeoutError:
            pass

        if time.monotonic() - start > _HARD_TIMEOUT:
            both.cancel()
            proc.kill()
            await proc.wait()
            _pending_terminal.pop(thread_id, None)
            logger.warning("terminal_command_timeout", extra={"thread_id": thread_id})
            return "Error: Command timed out after 60 seconds"

        if (
            proc.returncode is None
            and time.monotonic() - last_activity[0] > _IDLE_TIMEOUT
        ):
            if thread_id:
                _pending_terminal[thread_id] = {
                    "proc": proc,
                    "output_lines": output_lines,
                    "read_state": read_state,
                }
            return "\n".join(output_lines) + (
                "\n\n[Comando ainda rodando, sem output novo há alguns "
                "segundos — pode estar esperando input. Use "
                'terminal(stdin_input="...") para responder no mesmo '
                'processo, ou stdin_input="\\x03" para tentar Ctrl+C.]'
            )

    await proc.wait()
    return None


@vtool(
    extras=ToolExtras(
        render_hint="terminal_output",
        category="filesystem",
        destructive=True,
        icon="terminal",
        invalidates=["files", "diff"],
    )
)
async def terminal(
    ctx: ToolContext, command: str = "", stdin_input: str | None = None
) -> str:
    """Executa um comando shell de forma assíncrona (não bloqueia o event loop).

    Suporta comandos interativos: se um comando anterior nesta mesma thread
    ainda estiver rodando esperando input (ex.: prompt "continuar? [y/N]"),
    chame de novo passando só ``stdin_input`` (sem ``command``) para
    responder no MESMO processo, em vez de spawnar um comando novo.

    Args:
        command: Comando shell para executar. Vazio quando só respondendo
            a um prompt pendente via ``stdin_input``.
        stdin_input: Texto para escrever no stdin de um comando pendente
            desta thread (envia com quebra de linha automática). Requer que
            exista um comando ainda rodando e esperando input.

    Returns:
        Saída do comando (stdout + stderr) ou mensagem de erro se bloqueado
    """
    trust_err = _require_trust(ctx)
    if trust_err:
        return trust_err

    thread_id = ctx.thread_id or ""
    pending = _pending_terminal.get(thread_id) if thread_id else None

    if stdin_input is not None:
        if pending is None or pending["proc"].returncode is not None:
            return (
                "Error: não há comando pendente esperando input nesta sessão "
                "— chame `terminal` com um `command` novo."
            )
        proc = pending["proc"]
        output_lines = pending["output_lines"]
        if proc.stdin is None:
            _pending_terminal.pop(thread_id, None)
            return "Error: stdin do processo pendente não está disponível."
        try:
            proc.stdin.write((stdin_input + "\n").encode("utf-8"))
            await proc.stdin.drain()
        except Exception:
            _pending_terminal.pop(thread_id, None)
            return "Error: falha ao enviar input — o processo pode ter encerrado."

        start = time.monotonic()
        idle_result = await _drain_terminal_output(
            thread_id, proc, output_lines, start, read_state=pending.get("read_state")
        )
        if idle_result is not None:
            return idle_result
        _pending_terminal.pop(thread_id, None)
        output = "\n".join(output_lines)
        return output or f"Command executed with exit code {proc.returncode}"

    if not command:
        return "Error: informe `command` (ou `stdin_input` para responder a um comando pendente)."

    # Normaliza comandos Unix → Windows quando necessário
    if platform.system() == "Windows":
        command = re.sub(r"\bmkdir\s+-p\s+", "mkdir ", command)
        command = re.sub(r"\bmkdir\s+-p\s*$", "mkdir .", command)

    if not is_safe_shell_command(command):
        logger.warning(
            "terminal command blocked by safety check",
            extra={"command": command[:50]},
        )
        return (
            f"Error: Command '{command}' is blocked for safety. "
            "Destructive commands like rm -rf, mkfs, dd if=/dev/zero, "
            "and fork bombs are not permitted."
        )

    root, ws = _workspace_root(ctx)

    # Workspace remoto (SSH ou Codespace): delega via transport.
    # O streaming linha-a-linha e o stdin interativo não são suportados
    # nesse caminho; o output volta inteiro depois que o comando termina.
    transport = str(getattr(ws, "transport", "local"))
    if transport != "local":
        from backend.transport import get_transport

        backend_transport = get_transport(ws)
        cwd_remote = getattr(ws, "remote_path", None) or str(root)
        result = await backend_transport.run(
            ["sh", "-c", command],
            cwd=cwd_remote,
            timeout=30.0,
        )
        output = (result.stdout + result.stderr).strip()
        logger.info(
            "terminal_command_remote",
            extra={
                "command": command[:50],
                "exit_code": result.exit_code,
                "transport": transport,
            },
        )
        return output or f"Command executed with exit code {result.exit_code}"

    policy = parse_policy(root / "vectora.toml")
    if policy.enabled:
        # Roteia pelo worker jailado da workspace (backend.sandbox.
        # workspace_jail) em vez do subprocess direto abaixo. Comando
        # sandboxed roda até o fim numa chamada só — não registra nada em
        # _pending_terminal, então um `stdin_input` de acompanhamento (pra
        # responder a um prompt interativo) simplesmente não encontra
        # comando pendente e retorna o erro claro já existente logo no
        # topo desta função. Stdin interativo dentro do jail não é suportado.
        try:
            worker = await jail_manager.get_or_spawn(
                str(getattr(ws, "id", root)), str(root), policy
            )
            resp = await worker.request("exec", command=["sh", "-c", command])
        except WorkerSpawnError as exc:
            logger.warning(
                "terminal sandbox spawn failed", extra={"command": command[:50]}
            )
            return f"Error: {exc}"
        if "error" in resp:
            return f"Error: {resp['error']}"
        output = (resp.get("stdout", "") + resp.get("stderr", "")).strip()
        logger.info(
            "terminal_command_sandboxed",
            extra={"command": command[:50], "exit_code": resp.get("exit_code")},
        )
        return output or f"Command executed with exit code {resp.get('exit_code')}"

    proc: asyncio.subprocess.Process | None = None
    try:
        # asyncio.create_subprocess_shell não bloqueia o event loop
        # permitindo que o UI (Rich panels) e outras tarefas continuem rodando.
        # cwd confina o comando ao workspace ativo (scope guard rails).
        # stdin=PIPE viabiliza responder a prompts interativos (stdin_input).
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(root),
        )

        output_lines: list[str] = []
        start = time.monotonic()
        idle_result = await _drain_terminal_output(thread_id, proc, output_lines, start)
        if idle_result is not None:
            return idle_result

        output = "\n".join(output_lines)

        logger.info(
            "terminal_command_executed",
            extra={
                "command": command[:50],
                "exit_code": proc.returncode,
                "output_length": len(output),
            },
        )

        return output or f"Command executed with exit code {proc.returncode}"

    except Exception:
        logger.exception("terminal_command_failed", extra={"command": command[:50]})
        if thread_id:
            _pending_terminal.pop(thread_id, None)
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return "Error executing command. Check logs."


# ---------------------------------------------------------------------------
# Artifact helpers
# ---------------------------------------------------------------------------

_VALID_ARTIFACT_TYPES = VALID_ARTIFACT_TYPES


def _artifact_slug(title: str) -> str:
    """Converte título em slug kebab-case para nome de arquivo.

    Nunca trunca — títulos longos e descritivos são o comportamento
    desejado (ver tests/unit/test_artifact_slug.py, que existe
    especificamente para travar contra reintroduzir um limite de 50
    caracteres).
    """
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    return slug or "artifact"


# Slugs genéricos demais por tipo — título como "Plano" vira "plano.md", sem
# dizer do que trata. Força o modelo a escolher um título específico (ex:
# "Plano de Implementação do Jogo da Cobrinha em Godot 4.7").
_GENERIC_TITLE_SLUGS: dict[str, set[str]] = {
    "plan": {"plan", "plano", "planejamento", "plano-de-implementacao"},
    "spec": {"spec", "especificacao", "especificacoes", "especificacao-tecnica"},
    "task_list": {
        "tasks",
        "task-list",
        "tarefas",
        "todo",
        "todos",
        "lista-de-tarefas",
    },
    "overview": {"overview", "visao-geral", "resumo", "resumo-executivo"},
    "guide": {"guide", "guia", "tutorial"},
    "architecture": {"architecture", "arquitetura"},
    "implementation": {"implementation", "implementacao"},
}


def _rotate_artifact_history(artifact_dir: Path, slug: str, content: str) -> Path:
    """Grava ``content`` como a versão ATUAL (``{slug}.md``), preservando a
    versão anterior (se houver) como histórico imutável numerado.

    O arquivo sem sufixo é sempre a versão mais recente — a UI (Plan tab)
    consulta por ele diretamente. Antes de sobrescrever, a versão atual vira
    ``{slug}-N.md`` (N = próximo número livre), então o histórico cresce em
    ordem cronológica sem nunca perder uma versão anterior.
    """
    current = artifact_dir / f"{slug}.md"
    if current.exists():
        n = 1
        while (artifact_dir / f"{slug}-{n}.md").exists():
            n += 1
        current.rename(artifact_dir / f"{slug}-{n}.md")
    current.write_text(content, encoding="utf-8")
    return current


def _write_artifact_type_sidecar(
    artifact_dir: Path, slug: str, artifact_type: str
) -> None:
    """Grava `{slug}.artifact_type` — sidecar de texto puro com o tipo, lido
    pela API (`handlers/artifacts.py`) pra colorir/iconizar a Plan tab.
    Só existe pra versão ATUAL (`{slug}.md`); versões de histórico
    (`{slug}-N.md`) não têm sidecar próprio e caem no default ao serem
    listadas — aceitável, o histórico não é navegado por tipo."""
    (artifact_dir / f"{slug}.artifact_type").write_text(artifact_type, encoding="utf-8")


def _mirror_artifact_to_workspace(
    ctx: ToolContext, artifact_type: str, slug: str, content: str
) -> None:
    """Espelha a versão atual do artifact dentro do workspace ativo
    (``<workspace_root>/.vectora/{type}s/{slug}.md``) — sempre a última
    versão, sem histórico (o histórico imutável vive só em
    ``~/.vectora/artifacts/``). Só espelha quando a sessão tem um
    ``workspace_id`` explícito no ctx — sem isso, não força a criação de
    um workspace default só pra gravar o espelho. Best-effort: falha aqui
    nunca derruba a criação do artifact.
    """
    if not ctx.workspace_id:
        return
    try:
        root, _ws = _workspace_root(ctx)
        mirror_dir = root / ".vectora" / f"{artifact_type}s"
        mirror_dir.mkdir(parents=True, exist_ok=True)
        (mirror_dir / f"{slug}.md").write_text(content, encoding="utf-8")
    except Exception:
        logger.exception("create_artifact: falha ao espelhar '%s' no workspace", slug)


def _create_artifact_sync(
    ctx: ToolContext, artifact_type: str, slug: str, session_id: str, content: str
) -> Path:
    artifact_dir = Path.home() / ".vectora" / "artifacts" / session_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = _rotate_artifact_history(artifact_dir, slug, content)
    _write_artifact_type_sidecar(artifact_dir, slug, artifact_type)
    _mirror_artifact_to_workspace(ctx, artifact_type, slug, content)
    return path


@vtool(
    extras=ToolExtras(
        render_hint="artifact",
        category="artifacts",
        destructive=False,
        icon="file-code",
    )
)
async def create_artifact(
    artifact_type: str, title: str, content: str, ctx: ToolContext
) -> str:
    """Cria e persiste um artifact estruturado em ~/.vectora/artifacts/{session_id}/{slug}.md.

    Use esta tool quando o usuário pedir um documento que deve ser salvo permanentemente:
    plano de implementação, especificação técnica, lista de tarefas, visão geral de
    projeto, guia/tutorial, diagrama de arquitetura ou implementação de referência.
    NÃO use para respostas conversacionais — apenas para documentos que o usuário
    vai querer consultar depois.

    Versionamento: {slug}.md é sempre a versão MAIS RECENTE; ao salvar de novo com
    o mesmo título, a versão anterior vira histórico imutável ({slug}-1.md,
    {slug}-2.md, ...). Se a sessão tem um workspace ativo, a versão atual também é
    espelhada em <workspace>/.vectora/{artifact_type}s/{slug}.md (sem histórico —
    só a última versão, visível direto no projeto).

    Args:
        artifact_type: Tipo do artifact. Valores válidos:
            - "plan"           → plano de implementação, roadmap
            - "spec"           → especificação técnica, requisitos
            - "task_list"      → lista de tarefas, TODOs
            - "overview"       → visão geral de projeto, resumo executivo
            - "guide"          → guia, tutorial, how-to
            - "architecture"   → decisões de arquitetura, diagramas
            - "implementation" → código de referência, snippets documentados
        title: Título ESPECÍFICO e descritivo do artifact (ex: "Plano de
            Implementação do Jogo da Cobrinha em Godot 4.7"). Nunca use só o nome
            do tipo ("Plano", "Spec", "Tarefas") — isso é rejeitado com erro;
            o título vira o nome do arquivo e precisa dizer do que o documento trata.
        content: Conteúdo completo em markdown

    Returns:
        JSON com path, title, artifact_type, session_id e created_at
    """
    if artifact_type not in _VALID_ARTIFACT_TYPES:
        return json.dumps(
            {
                "error": f"artifact_type inválido: '{artifact_type}'. "
                f"Valores válidos: {sorted(_VALID_ARTIFACT_TYPES)}"
            }
        )

    if not title or not title.strip():
        return json.dumps({"error": "title não pode ser vazio"})

    if not content or not content.strip():
        return json.dumps({"error": "content não pode ser vazio"})

    session_id = ctx.thread_id
    if not session_id:
        return json.dumps(
            {"error": "Sessão não identificada — não foi possível salvar o artifact."}
        )

    slug = _artifact_slug(title.strip())
    if slug in _GENERIC_TITLE_SLUGS.get(artifact_type, set()):
        return json.dumps(
            {
                "error": (
                    f"title '{title.strip()}' é genérico demais — descreva do "
                    "que o artifact trata (ex.: 'Plano de Implementação do "
                    "Jogo da Cobrinha em Godot 4.7', não apenas 'Plano')."
                )
            }
        )

    try:
        stripped_content = content.strip()
        path = await asyncio.to_thread(
            _create_artifact_sync,
            ctx,
            artifact_type,
            slug,
            session_id,
            stripped_content,
        )

        created_at = datetime.now(UTC).isoformat()
        logger.info(
            "create_artifact: salvo '%s' (%s) → %s",
            title,
            artifact_type,
            path,
        )

        return json.dumps(
            {
                "path": str(path),
                "title": title.strip(),
                "artifact_type": artifact_type,
                "session_id": session_id,
                "created_at": created_at,
            },
            ensure_ascii=False,
        )

    except Exception as e:
        logger.exception("create_artifact: falha ao salvar '%s'", title)
        return json.dumps({"error": f"Falha ao salvar artifact: {e}"})

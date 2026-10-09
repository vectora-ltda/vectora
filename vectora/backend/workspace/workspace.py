"""WorkspaceRegistry — isolamento por projeto.

Cada diretório de trabalho tem um workspace único, identificado por um
sha256 truncado do caminho absoluto. Metadados ficam em
~/.vectora/workspaces.json e manifests em
~/.vectora/workspaces/<workspace_id>/.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal

from backend.settings import settings
from backend.vtypes import Workspace

logger = logging.getLogger(__name__)


def _workspaces_file() -> Path:
    """Caminho de ``workspaces.json``, sob ``settings.vectora_home``.

    Resolvido a cada chamada (não congelado em import) para respeitar
    ``VECTORA_HOME`` — tanto em subprocessos de teste que setam a env var
    antes do boot quanto em testes no mesmo processo que sobrescrevem
    ``settings.vectora_home`` diretamente.
    """
    return settings.vectora_home / "workspaces.json"


def _session_workspaces_root() -> Path:
    """Pasta base dos workspaces criados automaticamente por sessão.

    Por padrão mora em ``~/Documents/vectora`` (visível ao usuário no
    Explorer/Finder). Deriva de ``settings.vectora_home.parent`` em vez de
    ``Path.home()`` direto para respeitar ``VECTORA_HOME``: quando setado
    para um diretório isolado (testes), o Documents derivado cai dentro
    dessa mesma árvore isolada em vez de vazar para o home real.
    """
    return settings.vectora_home.parent / "Documents" / "vectora"


class WorkspaceRegistry:
    """Singleton que gerencia todos os workspaces do Vectora.

    Persiste em ~/.vectora/workspaces.json. Carregamento lazy — o arquivo
    só é lido na primeira operação que precisar dos dados.
    """

    _instance: ClassVar[WorkspaceRegistry | None] = None

    def __init__(self) -> None:
        self._workspaces: dict[str, Workspace] = {}
        #: workspace ativo por usuário ("local" quando sem auth)
        self._active: dict[str, str] = {}
        self._loaded = False

    @classmethod
    def instance(cls) -> WorkspaceRegistry:
        """Retorna a instância singleton do registry."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def derive_id(cwd: str) -> str:
        """Deriva workspace_id determinístico a partir do caminho absoluto."""
        normalized = str(Path(cwd).resolve())
        return hashlib.sha256(normalized.encode()).hexdigest()[:8]

    def _load(self) -> None:
        """Carrega workspaces.json (idempotente)."""
        if self._loaded:
            return
        workspaces_file = _workspaces_file()
        if workspaces_file.exists():
            try:
                data = json.loads(workspaces_file.read_text(encoding="utf-8"))
                for item in data.get("workspaces", []):
                    try:
                        ws = Workspace(**item)
                        # Nunca sobrescreve um registro já presente em
                        # memória: quem chamou `_load()` (via `get()`/
                        # `set()`) pode ter registrado uma versão mais nova
                        # do mesmo id antes do primeiro load lazy disparar.
                        self._workspaces.setdefault(ws.id, ws)
                    except Exception:
                        logger.debug("Workspace inválido ignorado: %s", item)
                active = data.get("active")
                if isinstance(active, dict):
                    self._active = {str(k): str(v) for k, v in active.items()}
            except Exception:
                logger.warning("Falha ao carregar workspaces.json", exc_info=True)
        self._loaded = True

    def _save(self) -> None:
        """Persiste workspaces.json."""
        try:
            workspaces_file = _workspaces_file()
            workspaces_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "workspaces": [ws.model_dump() for ws in self._workspaces.values()],
                "active": self._active,
            }
            workspaces_file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            logger.warning("Falha ao salvar workspaces.json", exc_info=True)

    def get_or_create(self, cwd: str | None = None) -> Workspace:
        """Retorna workspace existente ou cria um novo para o diretório.

        Ao criar, detecta automaticamente se o diretório contém um repositório
        git e preenche os campos is_git_repo, git_remote e git_current_branch.
        """
        self._load()
        resolved = str(Path(cwd).resolve() if cwd else Path.cwd())
        wid = self.derive_id(resolved)
        if wid not in self._workspaces:
            name = Path(resolved).name or "workspace"
            # Auto-detecção de git
            git_info: dict = {}
            try:
                from backend.tools.git import detect_git_info

                git_info = detect_git_info(resolved)
            except Exception:
                pass
            # O diretório onde o processo foi iniciado é implicitamente
            # confiável — quem tem shell ali já tem controle total. Pastas
            # adicionadas depois (via UI) exigem confirmação explícita.
            launched_here = resolved == str(Path.cwd().resolve())
            now = datetime.now(UTC).isoformat()
            ws = Workspace(
                id=wid,
                name=name,
                cwd=resolved,
                created_at=now,
                is_git_repo=git_info.get("is_git_repo", False),
                git_remote=git_info.get("git_remote"),
                git_current_branch=git_info.get("git_current_branch"),
                trusted=launched_here,
                trusted_at=now if launched_here else None,
                trusted_by="local" if launched_here else None,
            )
            self._workspaces[wid] = ws
            self._save()
            logger.info(
                "Workspace criado: %s (%s) git=%s",
                name,
                wid,
                ws.is_git_repo,
            )
        ws = self._workspaces[wid]
        if Path(ws.cwd).is_dir():
            self.ensure_local_files(ws)
        return ws

    def ensure_local_files(self, ws: Workspace) -> None:
        """Cria ``vectora.toml`` e ``.vectora/`` na pasta do workspace.

        Idempotente e nunca lança — falhas são apenas logadas.
        """
        try:
            from backend.workspace.workspace_config import ensure_workspace_files

            ensure_workspace_files(ws.cwd, name=ws.name)
        except Exception:
            logger.warning(
                "workspace: falha ao garantir arquivos locais de %s",
                ws.id,
                exc_info=True,
            )

    def get(self, workspace_id: str) -> Workspace | None:
        """Retorna workspace por ID ou None se não existir."""
        self._load()
        return self._workspaces.get(workspace_id)

    def claim_if_unowned(self, workspace_id: str, user_id: str) -> Workspace | None:
        """Atomically claim a legacy workspace for its first tool caller.

        Older registries may contain workspaces without ``owner_id``.  A
        caller that addresses one explicitly must establish ownership before
        Git tools use its repository; later callers are rejected by the normal
        owner check.  Existing owners are never changed.
        """
        # Resolve through ``get`` so embedders and tests that provide a
        # workspace backend continue to participate in the ownership check.
        ws = self.get(workspace_id)
        if ws is None:
            return None
        if ws.owner_id is None:
            ws.owner_id = user_id
            self._save()
        return ws

    def list_all(self) -> list[Workspace]:
        """Lista todos os workspaces registrados."""
        self._load()
        return list(self._workspaces.values())

    def rename(self, workspace_id: str, new_name: str) -> bool:
        """Renomeia um workspace. Retorna True se encontrado."""
        self._load()
        ws = self._workspaces.get(workspace_id)
        if ws is None:
            return False
        ws.name = new_name
        self._save()
        return True

    def delete(self, workspace_id: str) -> bool:
        """Remove um workspace do registry. Retorna True se existia."""
        self._load()
        if workspace_id not in self._workspaces:
            return False
        del self._workspaces[workspace_id]
        self._save()
        return True

    def bump_version(self, workspace_id: str) -> int:
        """Incrementa manifest_version e persiste. Retorna a nova versão.

        Chamado pelo curator após reescrever o MANIFEST.md. O orchestrator
        detecta o gap de versão e recarrega o contexto no próximo turno.
        """
        self._load()
        ws = self._workspaces.get(workspace_id)
        if ws is None:
            return 0
        ws.manifest_version += 1
        self._save()
        return ws.manifest_version

    def trust(self, workspace_id: str, user_id: str | None = None) -> bool:
        """Marca um workspace como confiável. Retorna True se encontrado.

        Tools de escrita, terminal e git só executam em workspaces confiáveis.
        """
        self._load()
        ws = self._workspaces.get(workspace_id)
        if ws is None:
            return False
        ws.trusted = True
        ws.trusted_at = datetime.now(UTC).isoformat()
        ws.trusted_by = user_id
        self._save()
        logger.info("Workspace confiado: %s (%s) by=%s", ws.name, workspace_id, user_id)
        return True

    def is_trusted(self, workspace_id: str) -> bool:
        """True se o workspace existe e foi marcado como confiável."""
        self._load()
        ws = self._workspaces.get(workspace_id)
        return ws is not None and ws.trusted

    def approve_hooks(self, workspace_id: str, user_id: str | None = None) -> bool:
        """Aprova a execução de ``[hooks].post_file_write`` deste workspace.

        Confiar na pasta (``trust``) não implica isso — hooks executam comando
        de shell arbitrário definido em ``vectora.toml``, que pode vir de um
        repositório clonado, não só de decisão local do usuário.
        """
        self._load()
        ws = self._workspaces.get(workspace_id)
        if ws is None:
            return False
        ws.hooks_approved = True
        ws.hooks_approved_at = datetime.now(UTC).isoformat()
        ws.hooks_approved_by = user_id
        self._save()
        logger.info("Hooks aprovados: %s (%s) by=%s", ws.name, workspace_id, user_id)
        return True

    def create(
        self,
        path: str,
        *,
        trust: bool = False,
        git_init: bool = False,
        user_id: str | None = None,
    ) -> Workspace:
        """Registra (ou recupera) um workspace para o diretório informado.

        Opcionalmente inicializa um repositório git na pasta e a marca como
        confiável. Re-detecta o estado git após um eventual ``git init``.
        """
        ws = self.get_or_create(path)
        if ws.owner_id is None and user_id is not None:
            # Primeira reivindicação vence — workspaces legados/sem dono
            # ficam livres pra qualquer request.create() os reivindicar.
            ws.owner_id = user_id
            self._save()
        if git_init and not ws.is_git_repo:
            try:
                from backend.tools.git import detect_git_info, git_init_repo

                git_init_repo(ws.cwd)
                info = detect_git_info(ws.cwd)
                ws.is_git_repo = info.get("is_git_repo", False)
                ws.git_current_branch = info.get("git_current_branch")
                ws.git_remote = info.get("git_remote")
                self._save()
            except Exception:
                logger.warning("git init falhou para %s", ws.cwd, exc_info=True)
        if trust:
            self.trust(ws.id, user_id)
        return self._workspaces[ws.id]

    def create_remote(
        self,
        *,
        name: str,
        transport: Literal["ssh", "codespace"],
        remote_host: str | None = None,
        remote_path: str | None = None,
        ssh_key_id: str | None = None,
        codespace_name: str | None = None,
        user_id: str | None = None,
    ) -> Workspace:
        """Registra um workspace remoto (SSH ou Codespace).

        ``cwd`` aponta para um placeholder local em
        ``~/.vectora/remote-workspaces/<id>/`` apenas para satisfazer
        consumidores que esperam um path (ex.: ``manifest_dir``). As
        tools que precisam do filesystem real usam ``get_transport()``
        e leem do host remoto via ``remote_path``.
        """
        self._load()
        if transport not in {"ssh", "codespace"}:
            raise ValueError(f"transport remoto inválido: {transport!r}")
        # ID derivado do par (transport, identificador único) — host pra
        # SSH, codespace_name pra Codespace. Idempotente: o mesmo par
        # sempre devolve o mesmo workspace.
        key = (
            f"ssh:{remote_host}:{remote_path or ''}"
            if transport == "ssh"
            else f"codespace:{codespace_name}"
        )
        wid = hashlib.sha256(key.encode()).hexdigest()[:8]
        if wid in self._workspaces:
            ws = self._workspaces[wid]
            if user_id is not None:
                self.trust(wid, user_id)
            return ws

        placeholder = settings.vectora_home / "remote-workspaces" / wid
        placeholder.mkdir(parents=True, exist_ok=True)

        ws = Workspace(
            id=wid,
            name=name or (remote_host or codespace_name or "remote"),
            cwd=str(placeholder),
            created_at=datetime.now(UTC).isoformat(),
            transport=transport,
            remote_host=remote_host,
            remote_path=remote_path,
            ssh_key_id=ssh_key_id,
            codespace_name=codespace_name,
        )
        if user_id is not None:
            ws.trusted = True
            ws.trusted_at = ws.created_at
            ws.trusted_by = user_id
            ws.owner_id = user_id
        self._workspaces[wid] = ws
        self._save()
        return ws

    def get_or_create_session_workspace(
        self, thread_id: str, user_id: str | None = None
    ) -> Workspace:
        """Workspace padrão de uma sessão: ``~/Documents/vectora/<thread_id>``.

        Registra o workspace e o marca como confiável, mas NÃO cria a pasta
        em disco imediatamente — a pasta só é materializada quando uma ferramenta
        de fs/git a usar pela primeira vez. Isso evita pastas órfãs em conversas
        que não utilizam o sistema de arquivos.
        """
        base = _session_workspaces_root() / thread_id
        # Não cria base.mkdir() aqui. A pasta é criada sob demanda:
        #   - escrita de arquivo: path.parent.mkdir(parents=True, exist_ok=True)
        #     em src/tools/fs.py já garante a criação na primeira operação real.
        #   - acesso de leitura/listagem a um workspace ainda vazio retorna erro
        #     adequado (diretório não encontrado), o que é o comportamento correto.
        return self.create(str(base), trust=True, user_id=user_id)

    def set_active(self, workspace_id: str, user_id: str | None = None) -> bool:
        """Define o workspace ativo do usuário. Retorna True se encontrado."""
        self._load()
        if workspace_id not in self._workspaces:
            return False
        self._active[user_id or "local"] = workspace_id
        self._save()
        # Avisa as demais réplicas — no modo lite é um no-op local.
        import json as _json

        from backend.persistence.kv import publish_soon

        publish_soon(
            "vectora:ws-active",
            _json.dumps({"user_id": user_id or "local", "workspace_id": workspace_id}),
        )
        return True

    def apply_remote_active(self, workspace_id: str, user_id: str) -> None:
        """Aplica troca de workspace ativo vinda de outra réplica.

        Atualiza só o estado local (sem ``_save`` nem re-publish — a réplica
        de origem já persistiu; salvar aqui causaria eco e corrida no JSON).
        """
        self._load()
        if workspace_id in self._workspaces:
            self._active[user_id] = workspace_id

    def get_active(self, user_id: str | None = None) -> Workspace | None:
        """Retorna o workspace ativo do usuário, ou None se não houver."""
        self._load()
        wid = self._active.get(user_id or "local")
        if wid is None:
            return None
        return self._workspaces.get(wid)


#: Singleton global — importar este objeto em vez de instanciar WorkspaceRegistry
workspace_registry: WorkspaceRegistry = WorkspaceRegistry.instance()


# ---------------------------------------------------------------------------
# Mutex por (workspace_id, thread_id) — serializa escritas concorrentes
# ---------------------------------------------------------------------------
#
# Tools que escrevem no filesystem do workspace (fs.py, git.py, terminal) e o
# rewind (que restaura snapshots/commits) competem pelo mesmo estado em disco.
# Sem serialização, um rewind disparado durante a execução de uma tool pode
# deixar o workspace num estado inconsistente (metade restaurado, metade
# sobrescrito pela tool em andamento).
#
# Uso típico:
#     async with acquire_workspace_lock(workspace_id, thread_id):
#         ...  # seção crítica: escreve no filesystem ou restaura checkpoint
#
# ``acquire_workspace_lock`` lança ``WorkspaceLockTimeoutError`` se não
# conseguir adquirir o lock dentro de ``timeout`` segundos — evita deadlock
# indefinido quando uma operação trava.

#: Segundos de espera antes de desistir de adquirir o lock.
DEFAULT_LOCK_TIMEOUT = 30.0


class WorkspaceLockTimeoutError(Exception):
    """Lançada quando o lock de (workspace_id, thread_id) não é adquirido a tempo."""

    def __init__(self, workspace_id: str, thread_id: str, timeout: float) -> None:
        self.workspace_id = workspace_id
        self.thread_id = thread_id
        self.timeout = timeout
        super().__init__(
            f"Não foi possível obter o lock do workspace {workspace_id!r} "
            f"(thread {thread_id!r}) em {timeout:.0f}s — outra operação "
            "ainda está em andamento."
        )


# Registro global de locks por chave "{workspace_id}:{thread_id}".
# Locks são criados sob demanda e nunca removidos — o overhead de manter um
# `asyncio.Lock` (alguns bytes) por par já visto é desprezível frente à
# complexidade de coordenar a remoção sob concorrência.
_workspace_locks: dict[str, asyncio.Lock] = {}
_workspace_locks_guard = asyncio.Lock()


def _lock_key(workspace_id: str, thread_id: str) -> str:
    return f"{workspace_id}:{thread_id}"


async def _get_workspace_lock(workspace_id: str, thread_id: str) -> asyncio.Lock:
    key = _lock_key(workspace_id, thread_id)
    lock = _workspace_locks.get(key)
    if lock is not None:
        return lock
    async with _workspace_locks_guard:
        lock = _workspace_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _workspace_locks[key] = lock
        return lock


@asynccontextmanager
async def acquire_workspace_lock(
    workspace_id: str,
    thread_id: str,
    *,
    timeout: float = DEFAULT_LOCK_TIMEOUT,  # noqa: ASYNC109
) -> AsyncGenerator[None]:
    """Adquire o mutex de ``(workspace_id, thread_id)`` com prazo-limite.

    Levanta ``WorkspaceLockTimeoutError`` se o lock continuar ocupado após
    ``timeout`` segundos — preferível a travar a request indefinidamente.
    """
    lock = await _get_workspace_lock(workspace_id, thread_id)
    try:
        async with asyncio.timeout(timeout):
            await lock.acquire()
    except TimeoutError as exc:
        raise WorkspaceLockTimeoutError(workspace_id, thread_id, timeout) from exc

    try:
        yield
    finally:
        lock.release()


def is_workspace_locked(workspace_id: str, thread_id: str) -> bool:
    """Indica se o par já tem uma operação em andamento (não bloqueia)."""
    lock = _workspace_locks.get(_lock_key(workspace_id, thread_id))
    return lock is not None and lock.locked()

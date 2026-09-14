"""SafeRootRegistry — pastas confiáveis configuráveis pelo admin.

Limita onde usuários comuns podem navegar ao criar workspaces. Admin
adiciona/remove raízes; o ``BrowseDir`` valida cada path requisitado
contra a lista.

Persiste em ``~/.vectora/safe_roots.json``. Carregamento lazy. Na
primeira inicialização, garante que ``~/Documents/vectora`` está como
entrada builtin (não removível, mas o label pode ser editado).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from backend.settings import settings
from backend.vtypes import SafeRoot

logger = logging.getLogger(__name__)


class SafeRootPersistenceError(RuntimeError):
    """Raised when the safe-root registry cannot be durably persisted."""


def _safe_roots_file() -> Path:
    """Caminho de ``safe_roots.json``, sob ``settings.vectora_home``.

    Resolvido a cada chamada (não congelado em import) para respeitar
    ``VECTORA_HOME`` em testes que sobrescrevem ``settings.vectora_home``.
    """
    return settings.vectora_home / "safe_roots.json"


def _default_builtin_root() -> Path:
    """Pasta builtin garantida — espelha o root de workspaces de sessão
    (``backend.workspace.workspace._session_workspaces_root``), mesma lógica
    de derivar de ``settings.vectora_home.parent`` para respeitar
    ``VECTORA_HOME`` sem mudar o default de produção (``~/Documents/vectora``).
    """
    return settings.vectora_home.parent / "Documents" / "vectora"


class SafeRootRegistry:
    """Singleton com persistência em JSON."""

    _instance: ClassVar[SafeRootRegistry | None] = None

    def __init__(self) -> None:
        self._roots: dict[str, SafeRoot] = {}
        self._loaded = False

    @classmethod
    def instance(cls) -> SafeRootRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def derive_id(path: str) -> str:
        """ID determinístico do path absoluto resolvido."""
        normalized = str(Path(path).expanduser().resolve())
        return hashlib.sha256(normalized.encode()).hexdigest()[:8]

    def _load(self) -> None:
        if self._loaded:
            return
        safe_roots_file = _safe_roots_file()
        if safe_roots_file.exists():
            try:
                data = json.loads(safe_roots_file.read_text(encoding="utf-8"))
                for item in data.get("roots", []):
                    try:
                        r = SafeRoot(**item)
                        self._roots[r.id] = r
                    except Exception:
                        logger.debug("SafeRoot inválido ignorado: %s", item)
            except Exception:
                logger.warning("Falha ao carregar safe_roots.json", exc_info=True)
        self._ensure_builtin()
        self._loaded = True

    def _ensure_builtin(self) -> None:
        """Garante a entrada builtin ~/Documents/vectora.

        Se já existir uma SafeRoot apontando para esse path (mesmo
        criada pelo admin), marca como builtin para proteger contra
        remoção acidental.
        """
        builtin_path = _default_builtin_root()
        builtin_id = self.derive_id(str(builtin_path))
        existing = self._roots.get(builtin_id)
        if existing is not None:
            if not existing.builtin:
                candidate = dict(self._roots)
                candidate[builtin_id] = existing.model_copy(update={"builtin": True})
                self._save_roots(candidate)
                self._roots = candidate
            return
        now = datetime.now(UTC).isoformat()
        builtin = SafeRoot(
            id=builtin_id,
            path=str(builtin_path.resolve()),
            label="Workspaces Vectora",
            created_at=now,
            created_by="system",
            builtin=True,
        )
        candidate = dict(self._roots)
        candidate[builtin_id] = builtin
        self._save_roots(candidate)
        self._roots = candidate

    def _save_roots(self, roots: dict[str, SafeRoot]) -> None:
        """Atomically persist a candidate registry or raise on failure."""
        safe_roots_file = _safe_roots_file()
        temporary_file = safe_roots_file.with_name(f".{safe_roots_file.name}.tmp")
        data = {"roots": [r.model_dump() for r in roots.values()]}
        try:
            safe_roots_file.parent.mkdir(parents=True, exist_ok=True)
            temporary_file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            temporary_file.replace(safe_roots_file)
        except Exception as exc:
            temporary_file.unlink(missing_ok=True)
            logger.warning("Falha ao salvar safe_roots.json", exc_info=True)
            raise SafeRootPersistenceError(
                "Não foi possível persistir as pastas seguras."
            ) from exc

    def _save(self) -> None:
        """Persist the current registry atomically."""
        self._save_roots(self._roots)

    # ----- API pública --------------------------------------------------

    def all_roots(self, *, include_archived: bool = False) -> list[SafeRoot]:
        """Retorna raízes ativas, ou também arquivadas quando solicitado."""
        self._load()
        return sorted(
            (
                r
                for r in self._roots.values()
                if include_archived or r.archived_at is None
            ),
            key=lambda r: (not r.builtin, r.label.lower()),
        )

    def get(self, root_id: str) -> SafeRoot | None:
        self._load()
        return self._roots.get(root_id)

    def add(self, path: str, label: str, user_id: str) -> SafeRoot:
        """Adiciona uma nova raiz. Idempotente por path (ID determinístico)."""
        self._load()
        resolved = str(Path(path).expanduser().resolve())
        root_id = self.derive_id(resolved)
        if root_id in self._roots:
            existing = self._roots[root_id]
            if existing.archived_at is not None:
                restored = existing.model_copy(update={"archived_at": None})
                candidate = dict(self._roots)
                candidate[root_id] = restored
                self._save_roots(candidate)
                self._roots = candidate
                return restored
            return existing
        root = SafeRoot(
            id=root_id,
            path=resolved,
            label=label.strip() or Path(resolved).name or resolved,
            created_at=datetime.now(UTC).isoformat(),
            created_by=user_id,
            builtin=False,
        )
        candidate = dict(self._roots)
        candidate[root_id] = root
        self._save_roots(candidate)
        self._roots = candidate
        return root

    def update_label(self, root_id: str, label: str) -> SafeRoot | None:
        """Renomeia uma entrada. Builtin permite renomear; remove não."""
        self._load()
        root = self._roots.get(root_id)
        if root is None:
            return None
        updated = root.model_copy(update={"label": label.strip() or root.label})
        candidate = dict(self._roots)
        candidate[root_id] = updated
        self._save_roots(candidate)
        self._roots = candidate
        return updated

    def remove(self, root_id: str) -> bool:
        """Remove raiz. Recusa se for builtin."""
        self._load()
        root = self._roots.get(root_id)
        if root is None:
            return False
        if root.builtin:
            return False
        candidate = dict(self._roots)
        del candidate[root_id]
        self._save_roots(candidate)
        self._roots = candidate
        return True

    def archive(self, root_id: str) -> SafeRoot | None:
        """Arquiva uma raiz sem apagar o registro ou seu histórico."""
        self._load()
        root = self._roots.get(root_id)
        if root is None or root.builtin:
            return None
        archived = root.model_copy(
            update={"archived_at": datetime.now(UTC).isoformat()}
        )
        candidate = dict(self._roots)
        candidate[root_id] = archived
        self._save_roots(candidate)
        self._roots = candidate
        return archived

    def restore(self, root_id: str) -> SafeRoot | None:
        """Restaura uma raiz arquivada."""
        self._load()
        root = self._roots.get(root_id)
        if root is None:
            return None
        restored = root.model_copy(update={"archived_at": None})
        candidate = dict(self._roots)
        candidate[root_id] = restored
        self._save_roots(candidate)
        self._roots = candidate
        return restored

    # ----- Validação ----------------------------------------------------

    def is_under_safe_root(self, path: str) -> SafeRoot | None:
        """Devolve o SafeRoot que contém ``path``, ou None se nenhum.

        Match inclui o próprio path quando igual à raiz (caso comum:
        usuário acabou de entrar na raiz e ainda não desceu).
        """
        self._load()
        target = Path(path).expanduser().resolve()
        for root in self._roots.values():
            if root.archived_at is not None:
                continue
            root_path = Path(root.path)
            try:
                target.relative_to(root_path)
                return root
            except ValueError:
                continue
        return None

    def closest_safe_root_for(self, path: str) -> SafeRoot | None:
        """Retorna o SafeRoot mais próximo do ``path`` solicitado.

        Útil para o fallback do BrowseDir: quando o caller pede um
        path fora dos limites, devolvemos a raiz mais relacionada
        (por exemplo, ``~/Documents/vectora`` quando o user pede ``~``).
        Se nenhuma raiz "contém" o caminho, devolve a primeira da lista.
        """
        self._load()
        contained = self.is_under_safe_root(path)
        if contained is not None:
            return contained
        # Sem match — devolve a builtin (primeiro item após sort).
        roots = self.all_roots()
        return roots[0] if roots else None


def get_safe_root_registry() -> SafeRootRegistry:
    """Atalho para o singleton (mesmo padrão do workspace_registry)."""
    return SafeRootRegistry.instance()

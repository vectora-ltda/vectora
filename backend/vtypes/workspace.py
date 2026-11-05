from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class Workspace(BaseModel):
    """Representa um workspace — projeto isolado por diretório (Pydantic)."""

    id: str = Field(description="ID do workspace (sha256[:8] do cwd).")
    name: str = Field(description="Nome do workspace/projeto.")
    cwd: str = Field(description="Diretório de trabalho correspondente.")
    created_at: str = Field(description="Timestamp ISO 8601 de criação.")
    bucket_names: list[str] = Field(
        default_factory=list, description="Buckets ativos associados."
    )
    manifest_version: int = Field(default=0, description="Versão atual do manifest.")

    # Preenchidos por detect_git_info ao criar o workspace.
    is_git_repo: bool = Field(
        default=False, description="True se o cwd é um repositório git."
    )
    git_remote: str | None = Field(
        default=None, description="URL do primeiro remote (origin)."
    )
    git_current_branch: str | None = Field(
        default=None, description="Branch ativa no momento do registro."
    )
    git_default_branch: str | None = Field(
        default=None, description="Branch padrão (main/master)."
    )

    trusted: bool = Field(
        default=False,
        description="True quando o usuário confiou na pasta. Tools de escrita, "
        "terminal e git só executam em workspaces confiáveis.",
    )
    trusted_at: str | None = Field(
        default=None, description="Timestamp ISO 8601 da confirmação de confiança."
    )
    trusted_by: str | None = Field(
        default=None, description="ID do usuário que confiou na pasta."
    )

    hooks_approved: bool = Field(
        default=False,
        description="True quando o usuário aprovou explicitamente a execução de "
        "hooks [hooks].post_file_write deste workspace (vectora.toml). Confiar "
        "no workspace não implica aprovar hooks — são efeitos colaterais "
        "distintos (write vs. shell arbitrário via config do repositório).",
    )
    hooks_approved_at: str | None = Field(
        default=None, description="Timestamp ISO 8601 da aprovação de hooks."
    )
    hooks_approved_by: str | None = Field(
        default=None, description="ID do usuário que aprovou os hooks."
    )

    owner_id: str | None = Field(
        default=None,
        description="ID do usuário que criou/reivindicou este workspace primeiro. "
        "None quando o workspace nunca foi reivindicado — nesse caso o acesso "
        "não é restringido por dono. Em modo servidor multi-usuário "
        "(VECTORA_AUTH_REQUIRED=true), um usuário autenticado só acessa "
        "workspaces com owner_id == seu próprio id (ou sem owner_id), exceto "
        "papéis root/admin.",
    )

    # Transporte do workspace. Workspaces remotos compartilham o mesmo
    # modelo; ``cwd`` é o caminho remoto (ou ponto de montagem local quando
    # o transport encapsula um túnel, ex.: Codespaces).
    transport: Literal["local", "ssh", "codespace"] = Field(
        default="local",
        description="Onde o filesystem do workspace vive: local, SSH ou GitHub Codespace.",
    )
    remote_host: str | None = Field(
        default=None,
        description="Para transport=ssh: 'user@host[:port]'. None caso contrário.",
    )
    remote_path: str | None = Field(
        default=None,
        description="Caminho absoluto no host remoto (transport=ssh|codespace).",
    )
    ssh_key_id: str | None = Field(
        default=None,
        description="ID da entry do vault KeePassXC que guarda a chave SSH.",
    )
    codespace_name: str | None = Field(
        default=None,
        description="Nome do GitHub Codespace (transport=codespace).",
    )

    def manifest_dir(self) -> Path:
        """Diretório de manifests do workspace."""
        return Path.home() / ".vectora" / "workspaces" / self.id

    def manifest_path(self) -> Path:
        """Caminho do MANIFEST.md principal."""
        return self.manifest_dir() / "MANIFEST.md"

    def bucket_manifest_path(self, bucket: str) -> Path:
        """Caminho do manifest de um bucket específico."""
        return self.manifest_dir() / "buckets" / f"{bucket}.md"

    def local_config_path(self) -> Path:
        """Caminho do ``vectora.toml`` na raiz do workspace."""
        return Path(self.cwd) / "vectora.toml"

    def local_dir(self) -> Path:
        """Pasta ``.vectora/`` na raiz do workspace (planos locais etc.)."""
        return Path(self.cwd) / ".vectora"

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

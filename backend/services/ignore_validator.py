"""Validador de padrões de ignore (gitignore, npmignore, dockerignore, etc).

Respeita múltiplos sistemas de ignore:
- .gitignore (git)
- .npmignore (npm)
- .dockerignore (Docker)
- .prettierignore (Prettier)
- .eslintignore (ESLint)
- .flake8ignore / .pylintrc (Python)
- .gitattributes (git attributes)

Previne que Vectora faça embedding de:
- node_modules/, venv/, .venv/, __pycache__/
- .env, .secrets, credentials
- build/, dist/, .build/ artifacts
- cache, tmp files
- private keys, SSH, certificates
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


class IgnorePatternValidator:
    """Validador multi-formato de padrões de ignore para RAG."""

    def __init__(self, base_dir: Path | None = None) -> None:
        """Inicializa o validador com padrões de ignore.

        Args:
            base_dir: Diretório base para procurar ignore files.
                     Se None, usa diretório atual.
        """
        self.base_dir = base_dir or Path.cwd()
        self.patterns: list[str] = []
        self.ignore_files = [
            ".gitignore",
            ".npmignore",
            ".dockerignore",
            ".prettierignore",
            ".eslintignore",
            ".gitattributes",
        ]

        # Padrões embutidos que sempre devem ser ignorados
        self.builtin_patterns = [
            # Diretórios Python
            "__pycache__",
            ".venv",
            "venv",
            ".egg-info",
            ".mypy_cache",
            ".pytest_cache",
            # Diretórios Node
            "node_modules",
            # Diretórios de build
            "build",
            "dist",
            ".build",
            "target",
            # Git
            ".git",
            ".github",
            # Arquivos de env
            ".env",
            # Credenciais e segurança
            ".secrets",
            ".pem",
            ".key",
            ".jks",
            "id_rsa",
            "id_ed25519",
            ".ssh",
            ".aws",
            ".kube",
            "credentials",
            "apikey",
            "api_key",
            "password",
            "token",
            "secret",
        ]

        self._load_ignore_patterns()

    def _load_ignore_patterns(self) -> None:
        """Carrega padrões de todos os arquivos de ignore disponíveis."""
        logger.debug(
            "Loading ignore patterns",
            extra={"base_dir": str(self.base_dir)},
        )

        # Carregar padrões embutidos (strings simples para substring matching)
        self.patterns = self.builtin_patterns.copy()

        # Carregar padrões de arquivos de ignore
        for ignore_file in self.ignore_files:
            ignore_path = self.base_dir / ignore_file
            if ignore_path.exists():
                try:
                    self._load_from_file(ignore_path)
                    logger.debug(f"Loaded patterns from {ignore_file}")
                except Exception as e:
                    logger.warning(f"Failed to load patterns from {ignore_file}: {e}")

        logger.info(
            "Ignore patterns loaded",
            extra={
                "total_patterns": len(self.patterns),
                "builtin_patterns": len(self.builtin_patterns),
            },
        )

    def _load_from_file(self, ignore_path: Path) -> None:
        """Carrega padrões de um arquivo de ignore.

        Args:
            ignore_path: Caminho do arquivo de ignore
        """
        with ignore_path.open(encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()

                # Ignorar linhas vazias e comentários
                if not line or line.startswith("#"):
                    continue

                # Remover ! (negation patterns - simplificamos ignorando por enquanto)
                if line.startswith("!"):
                    continue

                # Adicionar padrão simples como string (substring matching)
                # Remover slashes para padrões mais flexíveis
                clean_pattern = line.rstrip("/").lstrip("/")
                if clean_pattern and clean_pattern not in self.patterns:
                    self.patterns.append(clean_pattern)

    @staticmethod
    def _glob_to_regex(glob_pattern: str) -> str:
        """Converte padrão glob para regex (simplificado).

        Suporta:
        - * (qualquer coisa)
        - ** (qualquer coisa recursivo)

        Args:
            glob_pattern: Padrão glob

        Returns:
            Padrão regex equivalente
        """
        # Remover trailing slashes
        glob_pattern = glob_pattern.rstrip("/")

        # Escaper especiais de regex primeiro
        pattern = re.escape(glob_pattern)

        # Desescaper wildcards (que queremos como regex)
        pattern = pattern.replace(r"\*\*", ".*")  # ** = qualquer coisa (recursivo)
        pattern = pattern.replace(r"\*", "[^/]*")  # * = qualquer coisa except /

        # Se o padrão é simples (sem /), permite match em qualquer lugar
        if "/" not in glob_pattern and not glob_pattern.startswith("."):
            # Pattern como "node_modules" deve match "dir/node_modules/..."
            pattern = f"(^|.*/){pattern}(/.*)?$"
        elif glob_pattern.startswith("."):
            # Padrões que começam com . (como .env)
            pattern = f"(^|.*/){pattern}$"
        else:
            # Padrões com / - match normal
            pattern = f"^{pattern}(/.*)?$"

        return pattern

    def should_ignore(self, file_path: Path | str) -> bool:
        """Verifica se um arquivo deve ser ignorado.

        Args:
            file_path: Caminho do arquivo (absoluto ou relativo)

        Returns:
            True se arquivo deve ser ignorado, False caso contrário
        """
        if isinstance(file_path, str):
            file_path = Path(file_path)

        # Converter para string normalizada (lowercase for case-insensitive matching)
        path_str = str(file_path).replace("\\", "/").lower()

        # Verificar contra todos os padrões (agora são strings simples)
        for pattern in self.patterns:
            pattern_str = pattern.lower() if isinstance(pattern, str) else pattern

            # Substring matching - verifica se padrão está em qualquer parte do caminho
            if isinstance(pattern_str, str):
                if pattern_str in path_str or f"/{pattern_str}/" in f"/{path_str}/":
                    logger.debug(
                        "File should be ignored",
                        extra={"file": str(file_path), "pattern": pattern_str},
                    )
                    return True

        return False

    def filter_files(self, file_paths: list[Path | str]) -> list[Path]:
        """Filtra lista de arquivos removendo os que devem ser ignorados.

        Args:
            file_paths: Lista de caminhos de arquivos

        Returns:
            Lista filtrada apenas com arquivos que NÃO devem ser ignorados
        """
        result = []
        ignored_count = 0

        for file_path in file_paths:
            if not self.should_ignore(file_path):
                result.append(Path(file_path))
            else:
                ignored_count += 1

        if ignored_count > 0:
            logger.debug(
                f"Filtered out {ignored_count} ignored files",
                extra={"remaining": len(result)},
            )

        return result

    def get_ignored_patterns_summary(self) -> dict[str, int]:
        """Retorna resumo dos padrões carregados.

        Returns:
            Dicionário com contagem de padrões por tipo
        """
        return {
            "builtin_patterns": len(self.builtin_patterns),
            "total_patterns": len(self.patterns),
        }


# Singleton global
_validator: IgnorePatternValidator | None = None


def get_ignore_validator(base_dir: Path | None = None) -> IgnorePatternValidator:
    """Obtém instância singleton do validador de ignore.

    Args:
        base_dir: Diretório base (usado apenas na primeira inicialização)

    Returns:
        IgnorePatternValidator singleton
    """
    global _validator
    if _validator is None:
        _validator = IgnorePatternValidator(base_dir)
    return _validator

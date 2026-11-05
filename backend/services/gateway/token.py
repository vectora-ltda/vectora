import stat
from pathlib import Path


def load_token(path: Path) -> str | None:
    try:
        return path.read_text().strip() or None
    except FileNotFoundError:
        return None


def save_token(token: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token)
    # Remove permissão de leitura para outros (world-readable seria inseguro)
    current = path.stat().st_mode
    path.chmod(current & ~(stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH))

"""Select the update channel for a Vectora package version."""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from typing import Literal

UpdateChannel = Literal["maintenance", "latest"]
_VERSION = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$"
)


def select_update_channel(version: str) -> UpdateChannel:
    """Return the release stream for a strict ``major.minor.patch`` version."""
    match = _VERSION.fullmatch(version.strip())
    if match is None:
        raise ValueError(f"Versão do pacote inválida: {version!r}")
    if match.group("major") == "0" and match.group("minor") == "1":
        return "maintenance"
    return "latest"


def main(argv: Sequence[str] | None = None) -> int:
    """Print the selected channel for the workflow and return a shell status."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("Uso: select_update_channel.py MAJOR.MINOR.PATCH", file=sys.stderr)
        return 2
    try:
        print(select_update_channel(args[0]))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

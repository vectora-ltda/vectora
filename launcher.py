"""Entry-point do executável Vectora (empacotado por PyInstaller)."""

from __future__ import annotations

import os
import sys

# Ver backend/main.py — precisa estar setado antes do primeiro `import git`
# transitivo, e este é o ponto de entrada mais cedo do executável empacotado.
os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")

_here = os.path.dirname(os.path.abspath(__file__))
if os.path.isdir(_here) and _here not in sys.path:
    sys.path.insert(0, _here)

if __name__ == "__main__":
    from backend.main import run

    run()

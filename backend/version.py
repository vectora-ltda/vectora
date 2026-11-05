"""Version management for Vectora.

Dynamically reads version from pyproject.toml via importlib.metadata.
"""

import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_version


def get_vectora_version() -> str:
    """Get Vectora version from package metadata.

    Returns the version string from pyproject.toml.
    Falls back to "0.1.0" if package is not installed.

    Returns:
        Version string (e.g., "0.1.0")
    """
    try:
        return get_version("vectora")
    except PackageNotFoundError:
        return "0.1.0"


def get_build_version() -> str:
    """Versão completa com hash numérico de build (X.Y.Z.<hash>), quando
    ``VECTORA_BUILD_VERSION`` está definida no ambiente; senão cai pra
    versão semver sem hash. O binário Nuitka distribuído não carrega
    ``.git``, então esse hash não pode ser calculado em runtime.
    """
    return os.environ.get("VECTORA_BUILD_VERSION", get_vectora_version())


__version__ = get_vectora_version()

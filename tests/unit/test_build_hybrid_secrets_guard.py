"""Testes — build-hybrid.py::_assert_no_secrets_inside_backend.

build-hybrid.py fica na raiz do monorepo (fora de vectora/), sem suíte de
testes própria — importado aqui via importlib porque o nome do arquivo tem
hífen (não é um módulo Python válido pra `import` direto).

Guarda contra o Nuitka embutir segredo esquecido em backend/ dentro
do binário compilado — ver comentário de _FORBIDDEN_BACKEND_FILE_PATTERNS.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "build_hybrid", _ROOT / "build-hybrid.py"
)
assert _SPEC is not None and _SPEC.loader is not None
build_hybrid = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_hybrid)


def test_passes_clean_on_real_repo() -> None:
    """O repositório real não tem segredo esquecido dentro de backend/."""
    build_hybrid._assert_no_secrets_inside_backend()


def test_raises_when_env_file_planted_in_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """.env plantado em backend/ trava o build; defaults.env (isento) não conta."""
    fake_backend = tmp_path / "backend"
    fake_backend.mkdir()
    (fake_backend / "defaults.env").write_text("SAFE=1")
    monkeypatch.setattr(build_hybrid, "VECTORA", tmp_path)

    build_hybrid._assert_no_secrets_inside_backend()  # só defaults.env — não dispara

    (fake_backend / ".env").write_text("OPENAI_API_KEY=sk-real-secret")
    with pytest.raises(SystemExit) as exc_info:
        build_hybrid._assert_no_secrets_inside_backend()
    msg = str(exc_info.value)
    assert "backend" in msg
    assert "defaults.env" not in msg


def test_build_includes_test_runner_but_not_pytest_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "vectora"
    suite = project / "installed_tests" / "test_installed_runtime.py"
    suite.parent.mkdir(parents=True)
    suite.write_text("pytest suite", encoding="utf-8")
    (suite.parent / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"uv runtime")
    dist_dir = tmp_path / "dist" / "vectora"
    monkeypatch.setattr(build_hybrid, "VECTORA", project)
    monkeypatch.setattr(build_hybrid.shutil, "which", lambda name: str(uv))
    monkeypatch.setattr(build_hybrid.sys, "platform", "win32")

    build_hybrid._add_installed_test_runtime(dist_dir)

    runtime_dir = dist_dir / "vectora-test-runtime"
    assert (runtime_dir / "uv.exe").read_bytes() == b"uv runtime"
    assert (runtime_dir / suite.name).read_text(encoding="utf-8") == "pytest suite"
    assert (runtime_dir / "pytest.ini").read_text(encoding="utf-8") == "[pytest]\n"


def test_build_fails_if_uv_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "vectora"
    suite = project / "installed_tests" / "test_installed_runtime.py"
    suite.parent.mkdir(parents=True)
    suite.write_text("pytest suite", encoding="utf-8")
    (suite.parent / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    monkeypatch.setattr(build_hybrid, "VECTORA", project)
    monkeypatch.setattr(build_hybrid.shutil, "which", lambda _name: None)

    with pytest.raises(SystemExit, match="uv não encontrado"):
        build_hybrid._add_installed_test_runtime(tmp_path / "dist" / "vectora")

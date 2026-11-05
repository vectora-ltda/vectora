"""Backend macOS (Seatbelt/sandbox-exec) do sandbox. build_seatbelt_profile
testável em qualquer plataforma (gera texto SBPL, não executa); execução
real mockada — mesmo padrão de test_sandbox_docker.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.sandbox import macos as macos_mod
from backend.sandbox.macos import (
    _sbpl_escape,
    build_seatbelt_profile,
    run_macos_sandboxed,
)
from backend.sandbox.policy import SandboxPolicy


class TestBuildSeatbeltProfile:
    def test_starts_with_version_and_deny_default(self, tmp_path):
        profile = build_seatbelt_profile(SandboxPolicy(enabled=True), str(tmp_path))

        assert profile.startswith("(version 1)\n(deny default)")

    def test_allows_workspace_read_and_write(self, tmp_path):
        profile = build_seatbelt_profile(SandboxPolicy(enabled=True), str(tmp_path))

        ws = _sbpl_escape(str(tmp_path))
        assert f'(allow file-read* (subpath "{ws}"))' in profile
        assert f'(allow file-write* (subpath "{ws}"))' in profile

    def test_ro_paths_never_get_write_allow(self, tmp_path):
        ro_dir = tmp_path / "readonly"
        ro_dir.mkdir()
        profile = build_seatbelt_profile(
            SandboxPolicy(enabled=True, ro_paths=(str(ro_dir),)), str(tmp_path)
        )

        escaped = _sbpl_escape(str(ro_dir))
        assert f'(allow file-read* (subpath "{escaped}"))' in profile
        assert f'(allow file-write* (subpath "{escaped}"))' not in profile

    def test_rw_paths_get_both_read_and_write(self, tmp_path):
        rw_dir = tmp_path / "extra"
        rw_dir.mkdir()
        profile = build_seatbelt_profile(
            SandboxPolicy(enabled=True, rw_paths=(str(rw_dir),)), str(tmp_path)
        )

        escaped = _sbpl_escape(str(rw_dir))
        assert f'(allow file-read* (subpath "{escaped}"))' in profile
        assert f'(allow file-write* (subpath "{escaped}"))' in profile

    def test_lockdown_denies_network(self, tmp_path):
        profile = build_seatbelt_profile(
            SandboxPolicy(enabled=True, lockdown=True), str(tmp_path)
        )

        assert "(deny network-outbound)" in profile
        assert "(allow network-outbound)" not in profile

    def test_no_lockdown_allows_network(self, tmp_path):
        profile = build_seatbelt_profile(
            SandboxPolicy(enabled=True, lockdown=False), str(tmp_path)
        )

        assert "(allow network-outbound)" in profile

    def test_env_file_masked_by_default(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET=x")
        profile = build_seatbelt_profile(SandboxPolicy(enabled=True), str(tmp_path))

        escaped = _sbpl_escape(str(env_file))
        assert f'(deny file-read* (literal "{escaped}"))' in profile
        assert f'(deny file-write* (literal "{escaped}"))' in profile

    def test_vectora_toml_always_masked_even_without_explicit_mask(self, tmp_path):
        toml = tmp_path / "vectora.toml"
        toml.write_text("[sandbox]\n")
        # mask=() explícito — mesmo assim vectora.toml deve ser negado; o
        # worker nunca vê a própria política (mesmo invariante do bwrap).
        profile = build_seatbelt_profile(
            SandboxPolicy(enabled=True, mask=()), str(tmp_path)
        )

        escaped = _sbpl_escape(str(toml))
        assert f'(deny file-read* (literal "{escaped}"))' in profile

    def test_mask_deny_comes_after_workspace_allow(self, tmp_path):
        """SBPL é last-match-wins — o deny do arquivo mascarado precisa
        vir DEPOIS do allow amplo do workspace no texto do profile, senão
        o allow do workspace sobrescreveria o deny."""
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET=x")
        profile = build_seatbelt_profile(SandboxPolicy(enabled=True), str(tmp_path))

        ws_escaped = _sbpl_escape(str(tmp_path))
        env_escaped = _sbpl_escape(str(env_file))
        allow_idx = profile.index(f'(allow file-read* (subpath "{ws_escaped}"))')
        deny_idx = profile.index(f'(deny file-read* (literal "{env_escaped}"))')
        assert deny_idx > allow_idx

    def test_quotes_in_path_are_escaped(self, tmp_path):
        # Path fictício (não precisa existir em disco — Windows/NTFS nem
        # aceita `"` num nome de arquivo real) só pra exercitar o escape.
        weird = str(tmp_path / 'weird"name')
        profile = build_seatbelt_profile(
            SandboxPolicy(enabled=True, rw_paths=(weird,)), str(tmp_path)
        )

        assert '\\"' in profile
        assert 'weird"name' not in profile  # a aspa crua nunca aparece sem escape


@pytest.mark.asyncio
async def test_missing_sandbox_exec_returns_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(macos_mod.shutil, "which", lambda _name: None)

    result = await run_macos_sandboxed(
        ["ls"], str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert result.exit_code == 127
    assert "sandbox-exec" in result.stderr.lower()


@pytest.mark.asyncio
async def test_successful_run_returns_output(tmp_path, monkeypatch):
    monkeypatch.setattr(
        macos_mod.shutil,
        "which",
        lambda name: "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None,
    )
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"hi\n", b""))
    proc.returncode = 0
    monkeypatch.setattr(
        "backend.sandbox.macos.asyncio.create_subprocess_exec",
        AsyncMock(return_value=proc),
    )

    result = await run_macos_sandboxed(
        ["echo", "hi"], str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert result.exit_code == 0
    assert result.stdout == "hi\n"


@pytest.mark.asyncio
async def test_permission_denied_returns_clear_error_not_exception(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        macos_mod.shutil,
        "which",
        lambda name: "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None,
    )

    async def _raise_permission_denied(*_args, **_kwargs):
        raise PermissionError("Permission denied: sandbox-exec")

    monkeypatch.setattr(
        "backend.sandbox.macos.asyncio.create_subprocess_exec",
        _raise_permission_denied,
    )

    result = await run_macos_sandboxed(
        ["ls"], str(tmp_path), SandboxPolicy(enabled=True)
    )

    assert result.exit_code == 126
    assert "permissão" in result.stderr.lower()


@pytest.mark.asyncio
async def test_timeout_kills_process_and_reports_timed_out(tmp_path, monkeypatch):
    monkeypatch.setattr(
        macos_mod.shutil,
        "which",
        lambda name: "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None,
    )
    proc = MagicMock()

    async def _hang(*_args, **_kwargs):
        import asyncio

        await asyncio.sleep(10)

    proc.communicate = _hang
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    monkeypatch.setattr(
        "backend.sandbox.macos.asyncio.create_subprocess_exec",
        AsyncMock(return_value=proc),
    )

    result = await run_macos_sandboxed(
        ["sleep", "10"], str(tmp_path), SandboxPolicy(enabled=True), timeout_s=0.05
    )

    assert result.exit_code == 124
    assert result.timed_out is True
    proc.kill.assert_called_once()

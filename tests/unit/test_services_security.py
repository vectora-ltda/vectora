"""Tests for src/services/security.py"""

from __future__ import annotations

from pathlib import Path

from backend.services.security import (
    is_safe_file_path,
    is_safe_regex_pattern,
    is_sensitive_path,
)


class TestIsSensitivePath:
    def test_arquivo_comum_nao_e_sensivel(self, tmp_path):
        arquivo = tmp_path / "notas.txt"
        arquivo.write_text("ola")
        assert is_sensitive_path(arquivo) is False

    def test_dentro_de_ssh_e_sensivel(self, tmp_path):
        (tmp_path / ".ssh").mkdir()
        arquivo = tmp_path / ".ssh" / "id_rsa"
        assert is_sensitive_path(arquivo) is True

    def test_dentro_de_aws_e_sensivel(self, tmp_path):
        (tmp_path / ".aws").mkdir()
        arquivo = tmp_path / ".aws" / "credentials"
        assert is_sensitive_path(arquivo) is True

    def test_arquivo_dotenv_e_sensivel(self, tmp_path):
        arquivo = tmp_path / ".env"
        assert is_sensitive_path(arquivo) is True

    def test_arquivo_pem_e_sensivel(self, tmp_path):
        arquivo = tmp_path / "chave.pem"
        assert is_sensitive_path(arquivo) is True

    def test_erro_ao_resolver_falha_fechado(self, monkeypatch):
        def _resolve_falha(self):
            raise OSError("path inválido")

        monkeypatch.setattr(Path, "resolve", _resolve_falha)
        assert is_sensitive_path(Path("qualquer")) is True


class TestIsSafeFilePath:
    def test_path_within_allowed(self, tmp_path):
        allowed = str(tmp_path)
        target = str(tmp_path / "file.txt")
        assert is_safe_file_path(target, allowed_dirs=[allowed]) is True

    def test_path_outside_allowed(self, tmp_path):
        allowed = str(tmp_path / "subdir")
        target = str(tmp_path / "other" / "file.txt")
        assert is_safe_file_path(target, allowed_dirs=[allowed]) is False

    def test_empty_path_is_unsafe(self):
        assert is_safe_file_path("", allowed_dirs=["/allowed"]) is False

    def test_no_allowed_dirs_uses_defaults(self, tmp_path):
        # sem allowed_dirs explícito, usa defaults do settings
        result = is_safe_file_path(str(tmp_path / "file.txt"))
        assert isinstance(result, bool)

    def test_traversal_attempt(self, tmp_path):
        allowed = str(tmp_path / "safe")
        target = str(tmp_path / "safe" / ".." / ".." / "etc" / "passwd")
        result = is_safe_file_path(target, allowed_dirs=[allowed])
        assert result is False


class TestIsSafeRegexPattern:
    def test_simple_pattern_safe(self):
        assert is_safe_regex_pattern(r"hello\w+") is True

    def test_empty_pattern_safe(self):
        assert is_safe_regex_pattern("") is True

    def test_invalid_regex_returns_false(self):
        assert is_safe_regex_pattern(r"[invalid") is False

    def test_basic_pattern(self):
        assert is_safe_regex_pattern(r"\d+\.\d+") is True

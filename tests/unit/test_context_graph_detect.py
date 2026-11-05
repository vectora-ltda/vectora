"""Testes para backend/services/context_graph/detect.py.

Cobre: FileType, classify_file, _is_sensitive, _is_noise_dir, detect (API pública),
_file_within_size_cap, count_words, is_package_manifest_path stub,
google_workspace_enabled, _shebang_interpreter.
"""

from __future__ import annotations

from pathlib import Path


class TestFileType:
    def test_code_value(self):
        from backend.context_graph.detect import FileType

        assert FileType.CODE == "code"

    def test_document_value(self):
        from backend.context_graph.detect import FileType

        assert FileType.DOCUMENT == "document"

    def test_paper_value(self):
        from backend.context_graph.detect import FileType

        assert FileType.PAPER == "paper"


class TestGoogleWorkspaceStub:
    def test_disabled(self):
        from backend.context_graph.detect import google_workspace_enabled

        assert google_workspace_enabled() is False

    def test_convert_returns_none(self, tmp_path: Path):
        from backend.context_graph.detect import convert_google_workspace_file

        result = convert_google_workspace_file(tmp_path / "test.gdoc", tmp_path)
        assert result is None


class TestFileWithinSizeCap:
    def test_small_file_within_cap(self, tmp_path: Path):
        from backend.context_graph.detect import _file_within_size_cap

        f = tmp_path / "small.txt"
        f.write_bytes(b"hello")
        assert _file_within_size_cap(f, cap=100) is True

    def test_oversized_file_outside_cap(self, tmp_path: Path):
        from backend.context_graph.detect import _file_within_size_cap

        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * 200)
        assert _file_within_size_cap(f, cap=100) is False

    def test_missing_file_returns_false(self, tmp_path: Path):
        from backend.context_graph.detect import _file_within_size_cap

        assert _file_within_size_cap(tmp_path / "ghost.txt") is False


class TestClassifyFile:
    def test_python_is_code(self, tmp_path: Path):
        from backend.context_graph.detect import FileType, classify_file

        f = tmp_path / "app.py"
        f.write_text("print('hello')\n", encoding="utf-8")
        assert classify_file(f) == FileType.CODE

    def test_typescript_is_code(self, tmp_path: Path):
        from backend.context_graph.detect import FileType, classify_file

        f = tmp_path / "app.ts"
        f.write_text("export const x = 1;\n", encoding="utf-8")
        assert classify_file(f) == FileType.CODE

    def test_markdown_is_document(self, tmp_path: Path):
        from backend.context_graph.detect import FileType, classify_file

        f = tmp_path / "README.md"
        f.write_text("# Title\n", encoding="utf-8")
        assert classify_file(f) == FileType.DOCUMENT

    def test_yaml_is_document(self, tmp_path: Path):
        from backend.context_graph.detect import FileType, classify_file

        f = tmp_path / "config.yaml"
        f.write_text("key: value\n", encoding="utf-8")
        assert classify_file(f) == FileType.DOCUMENT

    def test_pdf_is_paper(self, tmp_path: Path):
        from backend.context_graph.detect import FileType, classify_file

        f = tmp_path / "paper.pdf"
        f.write_bytes(b"%PDF-1.4\n")
        assert classify_file(f) == FileType.PAPER

    def test_image_returns_image_type(self, tmp_path: Path):
        from backend.context_graph.detect import FileType, classify_file

        f = tmp_path / "icon.png"
        f.write_bytes(b"\x89PNG")
        result = classify_file(f)
        assert result is None or result == FileType.IMAGE

    def test_unknown_extension_returns_none(self, tmp_path: Path):
        from backend.context_graph.detect import classify_file

        f = tmp_path / "data.xyz123"
        f.write_text("data", encoding="utf-8")
        assert classify_file(f) is None

    def test_go_is_code(self, tmp_path: Path):
        from backend.context_graph.detect import FileType, classify_file

        f = tmp_path / "main.go"
        f.write_text("package main\n", encoding="utf-8")
        assert classify_file(f) == FileType.CODE


class TestIsSensitive:
    def test_env_file_is_sensitive(self, tmp_path: Path):
        from backend.context_graph.detect import _is_sensitive

        f = tmp_path / ".env"
        assert _is_sensitive(f) is True

    def test_env_local_is_sensitive(self, tmp_path: Path):
        from backend.context_graph.detect import _is_sensitive

        f = tmp_path / ".env.local"
        assert _is_sensitive(f) is True

    def test_pem_is_sensitive(self, tmp_path: Path):
        from backend.context_graph.detect import _is_sensitive

        f = tmp_path / "cert.pem"
        assert _is_sensitive(f) is True

    def test_ssh_key_is_sensitive(self, tmp_path: Path):
        from backend.context_graph.detect import _is_sensitive

        f = tmp_path / "id_rsa"
        assert _is_sensitive(f) is True

    def test_python_file_not_sensitive(self, tmp_path: Path):
        from backend.context_graph.detect import _is_sensitive

        f = tmp_path / "app.py"
        assert _is_sensitive(f) is False

    def test_ssh_dir_file_is_sensitive(self, tmp_path: Path):
        from backend.context_graph.detect import _is_sensitive

        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        f = ssh_dir / "known_hosts"
        assert _is_sensitive(f) is True


class TestIsNoiseDir:
    def test_node_modules_is_noise(self):
        from backend.context_graph.detect import _is_noise_dir

        assert _is_noise_dir("node_modules") is True

    def test_venv_is_noise(self):
        from backend.context_graph.detect import _is_noise_dir

        assert _is_noise_dir(".venv") is True

    def test_src_is_not_noise(self):
        from backend.context_graph.detect import _is_noise_dir

        assert _is_noise_dir("src") is False

    def test_dist_is_noise(self):
        from backend.context_graph.detect import _is_noise_dir

        assert _is_noise_dir("dist") is True

    def test_pycache_is_noise(self):
        from backend.context_graph.detect import _is_noise_dir

        assert _is_noise_dir("__pycache__") is True


class TestCountWords:
    def test_simple_text(self, tmp_path: Path):
        from backend.context_graph.detect import count_words

        f = tmp_path / "text.txt"
        f.write_text("one two three four\n", encoding="utf-8")
        assert count_words(f) == 4

    def test_empty_file(self, tmp_path: Path):
        from backend.context_graph.detect import count_words

        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        assert count_words(f) == 0

    def test_missing_file_returns_zero(self, tmp_path: Path):
        from backend.context_graph.detect import count_words

        assert count_words(tmp_path / "ghost.txt") == 0


class TestShebangInterpreter:
    def test_python_shebang(self, tmp_path: Path):
        from backend.context_graph.detect import _shebang_interpreter

        f = tmp_path / "script"
        f.write_text("#!/usr/bin/env python3\nprint('hi')\n", encoding="utf-8")
        result = _shebang_interpreter(f)
        assert result is not None
        assert "python" in result.lower()

    def test_no_shebang_returns_none(self, tmp_path: Path):
        from backend.context_graph.detect import _shebang_interpreter

        f = tmp_path / "noext"
        f.write_text("regular content\n", encoding="utf-8")
        assert _shebang_interpreter(f) is None


class TestDetect:
    def test_detects_python_files(self, tmp_path: Path):
        from backend.context_graph.detect import detect

        f = tmp_path / "app.py"
        f.write_text("def main(): pass\n", encoding="utf-8")
        result = detect(tmp_path)
        files = result.get("files", {})
        code_files = files.get("code", [])
        assert any("app.py" in str(p) for p in code_files)

    def test_skips_node_modules(self, tmp_path: Path):
        from backend.context_graph.detect import detect

        nm = tmp_path / "node_modules" / "lib"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("module.exports = {}\n", encoding="utf-8")
        result = detect(tmp_path)
        files = result.get("files", {})
        code_files = files.get("code", [])
        assert not any("node_modules" in str(p) for p in code_files)

    def test_skips_sensitive_files(self, tmp_path: Path):
        from backend.context_graph.detect import detect

        f = tmp_path / ".env"
        f.write_text("SECRET=abc\n", encoding="utf-8")
        result = detect(tmp_path)
        all_files = [
            str(p) for files in result.get("files", {}).values() for p in files
        ]
        assert not any(".env" in str(p) for p in all_files)

    def test_detects_markdown_files(self, tmp_path: Path):
        from backend.context_graph.detect import detect

        f = tmp_path / "README.md"
        f.write_text("# Hello\n", encoding="utf-8")
        result = detect(tmp_path)
        doc_files = result.get("files", {}).get("document", [])
        assert any("README.md" in str(p) for p in doc_files)

    def test_empty_dir_returns_empty_files(self, tmp_path: Path):
        from backend.context_graph.detect import detect

        result = detect(tmp_path)
        assert "files" in result
        total = sum(len(v) for v in result["files"].values())
        assert total == 0

    def test_detection_metadata_present(self, tmp_path: Path):
        from backend.context_graph.detect import detect

        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
        result = detect(tmp_path)
        assert "total_files" in result or "files" in result

    def test_follows_gitignore(self, tmp_path: Path):
        from backend.context_graph.detect import detect

        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("secret_impl.py\n", encoding="utf-8")
        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "secret_impl.py").write_text(
            "SECRET = 'hidden'\n", encoding="utf-8"
        )
        result = detect(tmp_path)
        code_files = result.get("files", {}).get("code", [])
        assert not any("secret_impl.py" in str(p) for p in code_files)

    def test_follows_vectoraignore(self, tmp_path: Path):
        # .vectoraignore é o ignore unificado do Vectora (vale também p/ RAG/fs);
        # o context graph deve respeitá-lo igual ao .gitignore.
        from backend.context_graph.detect import detect

        (tmp_path / ".vectoraignore").write_text(
            "ignored_by_vectora.py\n", encoding="utf-8"
        )
        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "ignored_by_vectora.py").write_text("y = 2\n", encoding="utf-8")
        result = detect(tmp_path)
        code_files = result.get("files", {}).get("code", [])
        assert any("app.py" in str(p) for p in code_files)
        assert not any("ignored_by_vectora.py" in str(p) for p in code_files)

    def test_vectoraignore_negation_wins_over_gitignore(self, tmp_path: Path):
        # last-match-wins: .vectoraignore é lido por último, então uma negação
        # nele re-inclui um arquivo excluído pelo .gitignore.
        from backend.context_graph.detect import detect

        (tmp_path / ".gitignore").write_text("*.gen.py\n", encoding="utf-8")
        (tmp_path / ".vectoraignore").write_text("!keep.gen.py\n", encoding="utf-8")
        (tmp_path / "drop.gen.py").write_text("a = 1\n", encoding="utf-8")
        (tmp_path / "keep.gen.py").write_text("b = 2\n", encoding="utf-8")
        result = detect(tmp_path)
        code_files = [str(p) for p in result.get("files", {}).get("code", [])]
        assert any("keep.gen.py" in p for p in code_files)
        assert not any("drop.gen.py" in p for p in code_files)

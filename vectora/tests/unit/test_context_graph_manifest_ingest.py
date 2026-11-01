"""Testes para backend/services/context_graph/manifest_ingest.py.

Cobre: is_package_manifest_path, extract_package_manifest (pyproject.toml,
go.mod, pom.xml, apm.yml, file too large, OSError, parse error, no name).
"""

from __future__ import annotations

from pathlib import Path


class TestIsPackageManifestPath:
    def test_pyproject_toml(self):
        from backend.context_graph.manifest_ingest import (
            is_package_manifest_path,
        )

        assert is_package_manifest_path(Path("pyproject.toml")) is True

    def test_go_mod(self):
        from backend.context_graph.manifest_ingest import (
            is_package_manifest_path,
        )

        assert is_package_manifest_path(Path("go.mod")) is True

    def test_pom_xml(self):
        from backend.context_graph.manifest_ingest import (
            is_package_manifest_path,
        )

        assert is_package_manifest_path(Path("pom.xml")) is True

    def test_apm_yaml(self):
        from backend.context_graph.manifest_ingest import (
            is_package_manifest_path,
        )

        assert is_package_manifest_path(Path("apm.yaml")) is True

    def test_random_file_not_manifest(self):
        from backend.context_graph.manifest_ingest import (
            is_package_manifest_path,
        )

        assert is_package_manifest_path(Path("requirements.txt")) is False

    def test_case_insensitive_pom(self):
        from backend.context_graph.manifest_ingest import (
            is_package_manifest_path,
        )

        assert is_package_manifest_path(Path("POM.XML")) is True

    def test_apm_uppercase(self):
        from backend.context_graph.manifest_ingest import (
            is_package_manifest_path,
        )

        assert is_package_manifest_path(Path("APM.YML")) is True


class TestExtractPackageManifestPyproject:
    def test_basic_pyproject(self, tmp_path: Path):
        from backend.context_graph.manifest_ingest import (
            extract_package_manifest,
        )

        p = tmp_path / "pyproject.toml"
        p.write_text(
            '[project]\nname = "mypackage"\nversion = "1.0.0"\n'
            'dependencies = ["requests", "fastapi"]\n',
            encoding="utf-8",
        )
        result = extract_package_manifest(p)
        assert len(result["nodes"]) >= 1
        assert any(n["label"] == "mypackage" for n in result["nodes"])

    def test_deps_as_edges(self, tmp_path: Path):
        from backend.context_graph.manifest_ingest import (
            extract_package_manifest,
        )

        p = tmp_path / "pyproject.toml"
        p.write_text(
            '[project]\nname = "myapp"\ndependencies = ["httpx>=0.24"]\n',
            encoding="utf-8",
        )
        result = extract_package_manifest(p)
        assert len(result["edges"]) >= 1
        assert any(e["relation"] == "depends_on" for e in result["edges"])

    def test_no_name_returns_empty(self, tmp_path: Path):
        from backend.context_graph.manifest_ingest import (
            extract_package_manifest,
        )

        p = tmp_path / "pyproject.toml"
        p.write_text("[tool.ruff]\nline-length = 88\n", encoding="utf-8")
        result = extract_package_manifest(p)
        assert result["nodes"] == []

    def test_version_stored(self, tmp_path: Path):
        from backend.context_graph.manifest_ingest import (
            extract_package_manifest,
        )

        p = tmp_path / "pyproject.toml"
        p.write_text('[project]\nname = "pkg"\nversion = "2.5.1"\n', encoding="utf-8")
        result = extract_package_manifest(p)
        pkg_node = next(n for n in result["nodes"] if n["label"] == "pkg")
        assert pkg_node.get("version") == "2.5.1"


class TestExtractPackageManifestGoMod:
    def test_basic_go_mod(self, tmp_path: Path):
        from backend.context_graph.manifest_ingest import (
            extract_package_manifest,
        )

        p = tmp_path / "go.mod"
        p.write_text(
            "module github.com/myorg/myapp\n\ngo 1.21\n\n"
            "require github.com/gin-gonic/gin v1.9.1\n",
            encoding="utf-8",
        )
        result = extract_package_manifest(p)
        assert len(result["nodes"]) >= 1
        node_labels = [n["label"] for n in result["nodes"]]
        assert any("myapp" in lbl for lbl in node_labels)

    def test_no_module_returns_empty(self, tmp_path: Path):
        from backend.context_graph.manifest_ingest import (
            extract_package_manifest,
        )

        p = tmp_path / "go.mod"
        p.write_text("go 1.21\n", encoding="utf-8")
        result = extract_package_manifest(p)
        assert result["nodes"] == []


class TestExtractPackageManifestPomXml:
    def test_basic_pom(self, tmp_path: Path):
        from backend.context_graph.manifest_ingest import (
            extract_package_manifest,
        )

        xml = (
            '<?xml version="1.0"?>\n'
            '<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
            "  <groupId>com.example</groupId>\n"
            "  <artifactId>myapp</artifactId>\n"
            "  <version>0.1.0</version>\n"
            "  <dependencies>\n"
            "    <dependency>\n"
            "      <groupId>org.springframework</groupId>\n"
            "      <artifactId>spring-core</artifactId>\n"
            "    </dependency>\n"
            "  </dependencies>\n"
            "</project>\n"
        )
        p = tmp_path / "pom.xml"
        p.write_text(xml, encoding="utf-8")
        result = extract_package_manifest(p)
        assert len(result["nodes"]) >= 1

    def test_invalid_xml_returns_error(self, tmp_path: Path):
        from backend.context_graph.manifest_ingest import (
            extract_package_manifest,
        )

        p = tmp_path / "pom.xml"
        p.write_text("<project><broken", encoding="utf-8")
        result = extract_package_manifest(p)
        assert "error" in result


class TestExtractPackageManifestApm:
    def test_basic_apm_yaml(self, tmp_path: Path):
        from backend.context_graph.manifest_ingest import (
            extract_package_manifest,
        )

        p = tmp_path / "apm.yml"
        p.write_text(
            "name: myservice\nversion: '1.0'\ndependencies:\n  - dep-a\n  - dep-b\n",
            encoding="utf-8",
        )
        result = extract_package_manifest(p)
        assert any(n["label"] == "myservice" for n in result["nodes"])
        assert len(result["edges"]) >= 1

    def test_apm_no_name_returns_empty(self, tmp_path: Path):
        from backend.context_graph.manifest_ingest import (
            extract_package_manifest,
        )

        p = tmp_path / "apm.yml"
        p.write_text("dependencies:\n  - dep-a\n", encoding="utf-8")
        result = extract_package_manifest(p)
        assert result["nodes"] == []


class TestExtractPackageManifestEdgeCases:
    def test_file_too_large_returns_error(self, tmp_path: Path):
        from backend.context_graph.manifest_ingest import (
            _MAX_MANIFEST_BYTES,
            extract_package_manifest,
        )

        p = tmp_path / "pyproject.toml"
        p.write_bytes(b"x" * (_MAX_MANIFEST_BYTES + 1))
        result = extract_package_manifest(p)
        assert "error" in result

    def test_oserror_returns_error(self, tmp_path: Path):
        from backend.context_graph.manifest_ingest import (
            extract_package_manifest,
        )

        p = tmp_path / "pyproject.toml"
        # Don't create the file
        result = extract_package_manifest(p)
        assert "error" in result

    def test_self_dep_ignored(self, tmp_path: Path):
        from backend.context_graph.manifest_ingest import (
            extract_package_manifest,
        )

        p = tmp_path / "pyproject.toml"
        p.write_text(
            '[project]\nname = "myapp"\ndependencies = ["myapp"]\n', encoding="utf-8"
        )
        result = extract_package_manifest(p)
        for e in result["edges"]:
            assert e["source"] != e["target"]


def test_maven_manifest_rejects_entity_expansion(tmp_path):
    from backend.context_graph.manifest_ingest import extract_package_manifest

    path = tmp_path / "pom.xml"
    path.write_text(
        '<!DOCTYPE project [<!ENTITY injected "untrusted-expanded-value">]><project><artifactId>&injected;</artifactId></project>',
        encoding="utf-8",
    )
    result = extract_package_manifest(path)
    assert result["nodes"] == []
    assert "error" in result
    assert "untrusted-expanded-value" not in str(result)

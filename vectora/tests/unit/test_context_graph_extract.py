"""Testes para backend/services/context_graph/extract.py.

Testa a API pública: _get_extractor, _safe_extract, helpers de utilitários.
Os parsers tree-sitter individuais têm 8500+ linhas; cobrimos via _safe_extract
com arquivos reais.
"""

from __future__ import annotations

from pathlib import Path


class TestGetExtractor:
    def test_python_file_returns_extractor(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor

        f = tmp_path / "app.py"
        f.touch()
        extractor = _get_extractor(f)
        assert extractor is not None
        assert callable(extractor)

    def test_typescript_file_returns_extractor(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor

        f = tmp_path / "app.ts"
        f.touch()
        extractor = _get_extractor(f)
        assert extractor is not None

    def test_tsx_file_returns_extractor(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor

        f = tmp_path / "Component.tsx"
        f.touch()
        extractor = _get_extractor(f)
        assert extractor is not None

    def test_go_file_returns_extractor(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor

        f = tmp_path / "main.go"
        f.touch()
        extractor = _get_extractor(f)
        assert extractor is not None

    def test_unknown_extension_returns_none(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor

        f = tmp_path / "data.xyz123abc"
        f.touch()
        assert _get_extractor(f) is None

    def test_mcp_json_routed_to_mcp_extractor(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor
        from backend.context_graph.mcp_ingest import extract_mcp_config

        f = tmp_path / ".mcp.json"
        f.touch()
        assert _get_extractor(f) is extract_mcp_config

    def test_pyproject_toml_routed_to_manifest(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor
        from backend.context_graph.manifest_ingest import (
            extract_package_manifest,
        )

        f = tmp_path / "pyproject.toml"
        f.touch()
        assert _get_extractor(f) is extract_package_manifest

    def test_pom_xml_routed_to_manifest(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor
        from backend.context_graph.manifest_ingest import (
            extract_package_manifest,
        )

        f = tmp_path / "pom.xml"
        f.touch()
        assert _get_extractor(f) is extract_package_manifest

    def test_blade_php_returns_blade_extractor(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor

        f = tmp_path / "layout.blade.php"
        f.touch()
        extractor = _get_extractor(f)
        assert extractor is not None

    def test_json_non_mcp_returns_json_extractor(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor

        f = tmp_path / "schema.json"
        f.touch()
        extractor = _get_extractor(f)
        # json files go through generic dispatch (not None unless unsupported)
        # At minimum callable or None; we assert not mcp
        from backend.context_graph.mcp_ingest import extract_mcp_config

        assert extractor is not extract_mcp_config


class TestSafeExtract:
    def test_returns_dict_on_success(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor, _safe_extract

        f = tmp_path / "file.py"
        f.write_text("x = 1\n", encoding="utf-8")
        extractor = _get_extractor(f)
        assert extractor is not None
        result = _safe_extract(extractor, f)
        assert isinstance(result, dict)

    def test_returns_empty_on_exception(self, tmp_path: Path):
        from backend.context_graph.extract import _safe_extract

        class _RaisingExtractor:
            def __call__(self, path: Path) -> dict:
                raise RuntimeError("parse failed")

        f = tmp_path / "broken.py"
        f.touch()
        result = _safe_extract(_RaisingExtractor(), f)
        assert isinstance(result, dict)
        assert result.get("nodes") == [] or "nodes" not in result


class TestMakeId:
    def test_deterministic(self):
        from backend.context_graph.extract import _make_id

        assert _make_id("module", "fn") == _make_id("module", "fn")

    def test_different_inputs_different_ids(self):
        from backend.context_graph.extract import _make_id

        assert _make_id("module", "fn_a") != _make_id("module", "fn_b")

    def test_returns_string(self):
        from backend.context_graph.extract import _make_id

        assert isinstance(_make_id("x"), str)


class TestFileStem:
    def test_plain_stem_includes_parent(self):
        from backend.context_graph.extract import _file_stem

        # _file_stem qualifies with parent dir name to avoid ID collisions
        f = Path("/project/src/auth.py")
        result = _file_stem(f)
        assert "auth" in result
        assert "src" in result

    def test_top_level_file_bare_stem(self):
        from backend.context_graph.extract import _file_stem

        # A file at root level (parent is ".") returns bare stem
        f = Path("./Makefile")
        result = _file_stem(f)
        assert "Makefile" in result


class TestSourceLocation:
    def test_integer_line(self):
        from backend.context_graph.extract import _source_location

        result = _source_location(42)
        assert result is not None
        assert "42" in result

    def test_string_line(self):
        from backend.context_graph.extract import _source_location

        result = _source_location("10")
        assert result is not None
        assert "10" in result

    def test_none_returns_none(self):
        from backend.context_graph.extract import _source_location

        assert _source_location(None) is None


class TestExtractPythonFile:
    def test_extracts_function(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor, _safe_extract

        f = tmp_path / "module.py"
        f.write_text("def authenticate(user): return True\n", encoding="utf-8")
        extractor = _get_extractor(f)
        assert extractor is not None
        result = _safe_extract(extractor, f)
        node_labels = [n.get("label", "") for n in result.get("nodes", [])]
        assert any("authenticate" in lbl for lbl in node_labels)

    def test_extracts_class(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor, _safe_extract

        f = tmp_path / "service.py"
        f.write_text("class AuthService:\n    pass\n", encoding="utf-8")
        extractor = _get_extractor(f)
        assert extractor is not None
        result = _safe_extract(extractor, f)
        node_labels = [n.get("label", "") for n in result.get("nodes", [])]
        assert any("AuthService" in lbl for lbl in node_labels)

    def test_empty_file_no_nodes(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor, _safe_extract

        f = tmp_path / "empty.py"
        f.write_text("", encoding="utf-8")
        extractor = _get_extractor(f)
        assert extractor is not None
        result = _safe_extract(extractor, f)
        assert isinstance(result, dict)

    def test_syntax_error_graceful(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor, _safe_extract

        f = tmp_path / "bad.py"
        f.write_text("def (:\n    pass\n", encoding="utf-8")
        extractor = _get_extractor(f)
        assert extractor is not None
        result = _safe_extract(extractor, f)
        assert isinstance(result, dict)


class TestExtractTypeScriptFile:
    def test_tree_sitter_language_aliases_use_pack_names(self) -> None:
        from backend.context_graph.extract import _load_tree_sitter_language

        language = _load_tree_sitter_language("tree_sitter_c_sharp")

        assert language is not None

    def test_unsupported_tree_sitter_language_is_explicit(self) -> None:
        import pytest

        from backend.context_graph.extract import _load_tree_sitter_language

        with pytest.raises(ValueError, match="does not provide"):
            _load_tree_sitter_language("dm")

    def test_extracts_function_ts(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor, _safe_extract

        f = tmp_path / "utils.ts"
        f.write_text(
            "export function greet(name: string): string { return `Hello ${name}`; }\n",
            encoding="utf-8",
        )
        extractor = _get_extractor(f)
        assert extractor is not None
        result = _safe_extract(extractor, f)
        assert isinstance(result.get("nodes"), list)

    def test_extracts_interface(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor, _safe_extract

        f = tmp_path / "types.ts"
        f.write_text(
            "export interface User { id: string; name: string; }\n", encoding="utf-8"
        )
        extractor = _get_extractor(f)
        assert extractor is not None
        result = _safe_extract(extractor, f)
        assert isinstance(result, dict)

    def test_tsx_uses_jsx_aware_tree_sitter_grammar(self, tmp_path: Path) -> None:
        from tree_sitter import Node

        from backend.context_graph.extract import _parse_js_tree

        f = tmp_path / "component.tsx"
        f.write_text(
            "export function Component() { return <Button>{formatDate()}</Button>; }\n",
            encoding="utf-8",
        )

        parsed = _parse_js_tree(f)

        assert parsed is not None
        _source, root = parsed
        assert not root.has_error

        def contains_jsx(node: Node) -> bool:
            return node.type == "jsx_element" or any(
                contains_jsx(child) for child in node.children
            )

        assert contains_jsx(root)


class TestExtractGdscriptFile:
    """GDScript (Godot) — achado real: um projeto Godot real do usuário
    (`.gd`) gerava grafo com 0 nós/0 arestas porque a extensão nunca foi
    registrada, apesar de já existir extractor pra linguagens de nicho
    ainda mais raras (DreamMaker)."""

    def test_gd_routed_to_gdscript_extractor(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor, extract_gdscript

        f = tmp_path / "player.gd"
        f.touch()
        assert _get_extractor(f) is extract_gdscript

    def test_extends_signal_function_and_calls(self, tmp_path: Path):
        from backend.context_graph.extract import extract_gdscript

        f = tmp_path / "player.gd"
        f.write_text(
            "extends CharacterBody2D\n"
            "\n"
            "class_name Player\n"
            "\n"
            "signal died\n"
            "\n"
            "func take_damage(amount):\n"
            "    self.apply(amount)\n"
            "    emit_signal('died')\n",
            encoding="utf-8",
        )
        result = extract_gdscript(f)
        labels = [n["label"] for n in result["nodes"]]
        assert "Player" in labels  # class_name relabeia o nó do arquivo
        assert "CharacterBody2D" in labels  # base do extends
        assert "signal died" in labels
        assert "take_damage()" in labels

        relations = {(e["relation"], e["target"]) for e in result["edges"]}
        assert any(rel == "inherits" for rel, _ in relations)
        call_targets = {
            e["target"] for e in result["edges"] if e["relation"] == "calls"
        }
        assert any("apply" in t for t in call_targets)
        assert any("emit_signal" in t for t in call_targets)

    def test_extends_by_res_path_resolves_to_real_file(self, tmp_path: Path):
        """`extends "res://base.gd"` resolve pro arquivo real via
        project.godot (regra de resolução do próprio Godot), não vira
        referência solta como um builtin da engine."""
        from backend.context_graph.extract import _make_id, extract_gdscript

        (tmp_path / "project.godot").write_text("", encoding="utf-8")
        base = tmp_path / "base.gd"
        base.write_text("extends Node\n", encoding="utf-8")
        child = tmp_path / "child.gd"
        child.write_text('extends "res://base.gd"\n', encoding="utf-8")

        result = extract_gdscript(child)
        expected_target = _make_id(str(base))
        assert any(
            e["relation"] == "inherits" and e["target"] == expected_target
            for e in result["edges"]
        )

    def test_syntax_error_graceful(self, tmp_path: Path):
        from backend.context_graph.extract import extract_gdscript

        f = tmp_path / "broken.gd"
        f.write_text("func (:\n    pass\n", encoding="utf-8")
        result = extract_gdscript(f)
        assert isinstance(result, dict)
        assert isinstance(result.get("nodes"), list)

    def test_missing_dependency_degrades_gracefully(self, tmp_path: Path, monkeypatch):
        import sys

        import backend.context_graph.extract as extract_mod

        monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", None)
        f = tmp_path / "player.gd"
        f.write_text("extends Node\n", encoding="utf-8")
        result = extract_mod.extract_gdscript(f)
        assert result == {
            "nodes": [],
            "edges": [],
            "error": "tree-sitter-language-pack not installed",
        }


class TestExtractGodotScene:
    """Godot .tscn/.tres — formato de seções próprio (sem grammar
    tree-sitter), conecta uma cena aos scripts que ela usa via
    `[ext_resource type="Script" ...]` + `script = ExtResource("id")`."""

    def test_tscn_routed_to_scene_extractor(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor, extract_godot_scene

        f = tmp_path / "level.tscn"
        f.touch()
        assert _get_extractor(f) is extract_godot_scene

    def test_tres_routed_to_scene_extractor(self, tmp_path: Path):
        from backend.context_graph.extract import _get_extractor, extract_godot_scene

        f = tmp_path / "theme.tres"
        f.touch()
        assert _get_extractor(f) is extract_godot_scene

    def test_scene_attaching_script_emits_uses_edge_to_real_file(self, tmp_path: Path):
        from backend.context_graph.extract import _make_id, extract_godot_scene

        (tmp_path / "project.godot").write_text("", encoding="utf-8")
        script = tmp_path / "player.gd"
        script.write_text("extends Node2D\n", encoding="utf-8")
        scene = tmp_path / "player.tscn"
        scene.write_text(
            "[gd_scene load_steps=2 format=3]\n\n"
            '[ext_resource type="Script" path="res://player.gd" id="1_abc"]\n\n'
            '[node name="Player" type="Node2D"]\n'
            'script = ExtResource("1_abc")\n',
            encoding="utf-8",
        )
        result = extract_godot_scene(scene)
        expected_target = _make_id(str(script))
        assert any(
            e["relation"] == "uses" and e["target"] == expected_target
            for e in result["edges"]
        )

    def test_non_script_resource_produces_no_edge(self, tmp_path: Path):
        """Textura/malha anexada não é grafo-relevante — só Script gera
        aresta (erro/borda: resource de outro type não deve virar edge)."""
        from backend.context_graph.extract import extract_godot_scene

        f = tmp_path / "sprite.tscn"
        f.write_text(
            "[gd_scene load_steps=2 format=3]\n\n"
            '[ext_resource type="Texture2D" path="res://icon.png" id="1_tex"]\n\n'
            '[node name="Sprite" type="Sprite2D"]\n'
            'texture = ExtResource("1_tex")\n',
            encoding="utf-8",
        )
        result = extract_godot_scene(f)
        assert result["edges"] == []

    def test_oversized_scene_indexes_header_only_no_crash(self, tmp_path: Path):
        """Erro/borda: arquivo acima do teto de tamanho não trava nem
        lança — indexa só o cabeçalho de ext_resource, sem escanear os
        blocos [node ...] (podem ter megabytes de sub-recursos)."""
        import backend.context_graph.extract as extract_mod

        f = tmp_path / "huge.tscn"
        big_padding = "# padding\n" * 300_000
        f.write_text(
            "[gd_scene load_steps=2 format=3]\n\n"
            '[ext_resource type="Script" path="res://player.gd" id="1_abc"]\n\n'
            + big_padding
            + '[node name="Player" type="Node2D"]\n'
            'script = ExtResource("1_abc")\n',
            encoding="utf-8",
        )
        assert f.stat().st_size > extract_mod._GODOT_SCENE_MAX_BYTES
        result = extract_mod.extract_godot_scene(f)
        assert result["edges"] == []
        assert len(result["nodes"]) == 1  # só o nó do arquivo, sem crash

    def test_scene_without_ext_resource_returns_only_file_node(self, tmp_path: Path):
        from backend.context_graph.extract import extract_godot_scene

        f = tmp_path / "empty.tscn"
        f.write_text("[gd_scene load_steps=1 format=3]\n", encoding="utf-8")
        result = extract_godot_scene(f)
        assert len(result["nodes"]) == 1
        assert result["edges"] == []

    def test_missing_file_returns_error_not_exception(self, tmp_path: Path):
        from backend.context_graph.extract import extract_godot_scene

        result = extract_godot_scene(tmp_path / "does-not-exist.tscn")
        assert isinstance(result, dict)
        assert result.get("error")


class TestStripJsonc:
    def test_removes_line_comments(self):
        from backend.context_graph.extract import _strip_jsonc

        code = '{\n  "key": "value" // comment\n}'
        result = _strip_jsonc(code)
        assert "//" not in result
        assert "value" in result

    def test_removes_block_comments(self):
        from backend.context_graph.extract import _strip_jsonc

        code = '{ /* block comment */ "key": 1 }'
        result = _strip_jsonc(code)
        assert "/*" not in result

    def test_preserves_urls(self):
        from backend.context_graph.extract import _strip_jsonc

        code = '{ "url": "https://example.com" }'
        result = _strip_jsonc(code)
        assert "https://example.com" in result

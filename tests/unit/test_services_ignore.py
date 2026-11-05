"""Testes para src/services/ignore.py — foco nas funções novas.

Cobre:
- load_vectoraignore_spec: carrega .vectoraignore com pathspec gitwildmatch
- load_ignore_spec: combina .gitignore + .vectoraignore num spec único
- Integração: is_ignored respeita spec combinado
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestLoadVectoraignoreSpec:
    """load_vectoraignore_spec: carrega .vectoraignore como PathSpec."""

    def test_returns_none_when_no_file(self, tmp_path):
        from backend.services.ignore import load_vectoraignore_spec

        spec = load_vectoraignore_spec(tmp_path)
        assert spec is None

    def test_loads_simple_pattern(self, tmp_path):
        from backend.services.ignore import load_vectoraignore_spec

        (tmp_path / ".vectoraignore").write_text("*.log\n")
        spec = load_vectoraignore_spec(tmp_path)
        assert spec is not None
        assert spec.match_file("error.log")
        assert not spec.match_file("main.py")

    def test_loads_directory_pattern(self, tmp_path):
        from backend.services.ignore import load_vectoraignore_spec

        (tmp_path / ".vectoraignore").write_text("tests/fixtures/**\n")
        spec = load_vectoraignore_spec(tmp_path)
        assert spec is not None
        assert spec.match_file("tests/fixtures/data.json")
        assert not spec.match_file("src/main.py")

    def test_ignores_comment_lines(self, tmp_path):
        from backend.services.ignore import load_vectoraignore_spec

        (tmp_path / ".vectoraignore").write_text("# comment\n*.generated.py\n")
        spec = load_vectoraignore_spec(tmp_path)
        assert spec is not None
        assert spec.match_file("schema.generated.py")
        assert not spec.match_file("models.py")

    def test_searches_up_to_parent(self, tmp_path):
        from backend.services.ignore import load_vectoraignore_spec

        (tmp_path / ".vectoraignore").write_text("*.secret\n")
        subdir = tmp_path / "src" / "core"
        subdir.mkdir(parents=True)

        spec = load_vectoraignore_spec(subdir)
        assert spec is not None
        assert spec.match_file("credentials.secret")

    def test_uses_closest_vectoraignore(self, tmp_path):
        """Quando há dois .vectoraignore em níveis distintos, usa o mais próximo."""
        from backend.services.ignore import load_vectoraignore_spec

        (tmp_path / ".vectoraignore").write_text("*.parent\n")
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / ".vectoraignore").write_text("*.child\n")

        spec = load_vectoraignore_spec(subdir)
        assert spec is not None
        # bate padrão do filho
        assert spec.match_file("a.child")
        # NÃO bate padrão do pai (usa apenas o mais próximo)
        assert not spec.match_file("a.parent")


class TestLoadIgnoreSpec:
    """load_ignore_spec: combina .gitignore + .vectoraignore."""

    def test_returns_none_when_neither_exists(self, tmp_path):
        from backend.services.ignore import load_ignore_spec

        spec = load_ignore_spec(tmp_path)
        assert spec is None

    def test_uses_gitignore_when_only_that_exists(self, tmp_path):
        from backend.services.ignore import load_ignore_spec

        (tmp_path / ".gitignore").write_text("*.pyc\n")
        spec = load_ignore_spec(tmp_path)
        assert spec is not None
        assert spec.match_file("main.pyc")
        assert not spec.match_file("main.py")

    def test_uses_vectoraignore_when_only_that_exists(self, tmp_path):
        from backend.services.ignore import load_ignore_spec

        (tmp_path / ".vectoraignore").write_text("data/**\n")
        spec = load_ignore_spec(tmp_path)
        assert spec is not None
        assert spec.match_file("data/dump.json")
        assert not spec.match_file("src/model.py")

    def test_combines_both_specs(self, tmp_path):
        from backend.services.ignore import load_ignore_spec

        (tmp_path / ".gitignore").write_text("*.pyc\ndist/\n")
        (tmp_path / ".vectoraignore").write_text("tests/fixtures/**\n*.generated.py\n")
        spec = load_ignore_spec(tmp_path)
        assert spec is not None

        # padrões do .gitignore
        assert spec.match_file("foo.pyc")
        assert spec.match_file("dist/bundle.js")

        # padrões do .vectoraignore
        assert spec.match_file("tests/fixtures/data.json")
        assert spec.match_file("schema.generated.py")

        # arquivo não ignorado por nenhum
        assert not spec.match_file("src/core/models.py")

    def test_gitignore_and_vectoraignore_independent_search(self, tmp_path):
        """Cada arquivo é procurado independentemente na hierarquia."""
        from backend.services.ignore import load_ignore_spec

        parent = tmp_path / "proj"
        parent.mkdir()
        (parent / ".gitignore").write_text("*.log\n")

        subdir = parent / "src"
        subdir.mkdir()
        (subdir / ".vectoraignore").write_text("*.secret\n")

        # subdir tem .vectoraignore local; sobe e acha .gitignore no proj/
        spec = load_ignore_spec(subdir)
        assert spec is not None
        assert spec.match_file("error.log")  # do .gitignore em proj/
        assert spec.match_file("key.secret")  # do .vectoraignore em src/


class TestIsIgnoredWithCombinedSpec:
    """Garante que is_ignored funciona com spec combinado de load_ignore_spec."""

    def test_file_ignored_by_vectoraignore(self, tmp_path):
        from backend.services.ignore import is_ignored, load_ignore_spec

        (tmp_path / ".vectoraignore").write_text("*.dump\n")
        spec = load_ignore_spec(tmp_path)

        dump_file = tmp_path / "db.dump"
        assert is_ignored(dump_file, tmp_path, spec)

    def test_file_not_ignored(self, tmp_path):
        from backend.services.ignore import is_ignored, load_ignore_spec

        (tmp_path / ".vectoraignore").write_text("*.dump\n")
        spec = load_ignore_spec(tmp_path)

        py_file = tmp_path / "main.py"
        assert not is_ignored(py_file, tmp_path, spec)

    def test_always_skip_dirs_still_apply(self, tmp_path):
        """ALWAYS_SKIP_DIRS são bloqueados mesmo sem spec."""
        from backend.services.ignore import is_ignored

        node_modules_file = tmp_path / "node_modules" / "pkg" / "index.js"
        assert is_ignored(node_modules_file, tmp_path, None)


class TestWalkFilesPruning:
    """walk_files/iter_files: poda de diretórios DURANTE o os.walk.

    Garante que node_modules/.venv/etc. e dirs do .gitignore nem são
    varridos — não apenas filtrados a posteriori, que era a causa do
    congelamento de grep/list_dir/ingest_docs em repositórios grandes.
    """

    def test_never_descends_into_always_skip_dirs(self, tmp_path, monkeypatch):
        """os.walk nunca visita o interior de node_modules (poda real)."""
        import os as os_module

        deep = tmp_path / "node_modules" / "pkg" / "deep"
        deep.mkdir(parents=True)
        (deep / "index.js").write_text("x")
        (tmp_path / "main.py").write_text("x")

        visited: list[str] = []
        original_walk = os_module.walk

        def spy_walk(top, *args, **kwargs):
            for dirpath, dirnames, filenames in original_walk(top, *args, **kwargs):
                visited.append(str(dirpath))
                # yield das MESMAS listas para a poda in-place propagar
                yield dirpath, dirnames, filenames

        monkeypatch.setattr("os.walk", spy_walk)

        from backend.services.ignore import iter_files

        files = iter_files(tmp_path)
        assert files == [tmp_path / "main.py"]
        assert not any("node_modules" in v for v in visited), (
            "walk desceu em node_modules — poda não está funcionando"
        )

    def test_never_descends_into_gitignored_dirs(self, tmp_path, monkeypatch):
        """Dirs batidos pelo .gitignore também são podados, não só filtrados."""
        import os as os_module

        (tmp_path / ".gitignore").write_text("generated/\n")
        gen = tmp_path / "generated" / "sub"
        gen.mkdir(parents=True)
        (gen / "out.md").write_text("x")
        (tmp_path / "doc.md").write_text("x")

        visited: list[str] = []
        original_walk = os_module.walk

        def spy_walk(top, *args, **kwargs):
            for dirpath, dirnames, filenames in original_walk(top, *args, **kwargs):
                visited.append(str(dirpath))
                yield dirpath, dirnames, filenames

        monkeypatch.setattr("os.walk", spy_walk)

        from backend.services.ignore import iter_files, load_ignore_spec

        spec = load_ignore_spec(tmp_path)
        files = iter_files(tmp_path, "**/*.md", spec)
        assert files == [tmp_path / "doc.md"]
        assert not any("generated" in v for v in visited), (
            "walk desceu em dir do .gitignore — poda não está funcionando"
        )

    def test_skipped_count_includes_pruned_dirs_and_ignored_files(self, tmp_path):
        """skipped_ignored = dirs podados (1 por subárvore) + arquivos do spec."""
        from backend.services.ignore import load_ignore_spec, walk_files

        (tmp_path / ".gitignore").write_text("*.log\nvendor/\n")
        (tmp_path / "node_modules").mkdir()  # podado → +1
        (tmp_path / "vendor").mkdir()  # podado via gitignore → +1
        (tmp_path / "error.log").write_text("x")  # ignorado pelo spec → +1
        (tmp_path / "main.py").write_text("x")  # mantido

        spec = load_ignore_spec(tmp_path)
        files, skipped = walk_files(tmp_path, "**/*", spec)
        # o próprio .gitignore não é ignorado e entra na listagem
        assert set(files) == {tmp_path / ".gitignore", tmp_path / "main.py"}
        assert skipped == 3

    def test_include_dirs_lists_kept_directories(self, tmp_path):
        """include_dirs=True retorna dirs não podados (uso do list_dir)."""
        from backend.services.ignore import walk_files

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("x")
        (tmp_path / "node_modules").mkdir()

        entries, _ = walk_files(tmp_path, "**/*", None, include_dirs=True)
        assert tmp_path / "src" in entries
        assert tmp_path / "src" / "app.py" in entries
        assert tmp_path / "node_modules" not in entries

    def test_glob_pattern_filters_files_only(self, tmp_path):
        """O glob filtra por nome de arquivo (ex: **/*.py)."""
        from backend.services.ignore import walk_files

        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.md").write_text("x")

        files, _ = walk_files(tmp_path, "**/*.py", None)
        assert files == [tmp_path / "a.py"]


class TestVectoraStateDirExemption:
    """A pasta `.vectora` (plans/memory) é SEMPRE acessível — a obediência
    ao `.gitignore` nunca pode escondê-la, mesmo que `.vectora/` esteja
    listada no `.gitignore` do projeto."""

    def test_is_ignored_false_mesmo_com_gitignore_listando_vectora(self, tmp_path):
        from backend.services.ignore import is_ignored, load_ignore_spec

        (tmp_path / ".gitignore").write_text(".vectora/\n")
        spec = load_ignore_spec(tmp_path)

        plan = tmp_path / ".vectora" / "plans" / "plan.md"
        assert is_ignored(plan, tmp_path, spec) is False

    def test_is_ignored_false_sem_spec_tambem(self, tmp_path):
        """Sem .gitignore nenhum, `.vectora` obviamente continua acessível."""
        from backend.services.ignore import is_ignored

        plan = tmp_path / ".vectora" / "memory" / "MEMORY.md"
        assert is_ignored(plan, tmp_path, None) is False

    def test_walk_desce_em_vectora_mesmo_gitignorada(self, tmp_path, monkeypatch):
        """`walk_files` varre o interior de `.vectora` mesmo com `.vectora/`
        no `.gitignore` — o plano/memória do Vectora nunca é podado."""
        import os as os_module

        (tmp_path / ".gitignore").write_text(".vectora/\n")
        plan_dir = tmp_path / ".vectora" / "plans"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan.md").write_text("x")
        (tmp_path / "README.md").write_text("x")

        visited: list[str] = []
        original_walk = os_module.walk

        def spy_walk(top, *args, **kwargs):
            for dirpath, dirnames, filenames in original_walk(top, *args, **kwargs):
                visited.append(str(dirpath))
                yield dirpath, dirnames, filenames

        monkeypatch.setattr("os.walk", spy_walk)

        from backend.services.ignore import iter_files, load_ignore_spec

        spec = load_ignore_spec(tmp_path)
        files = iter_files(tmp_path, "**/*.md", spec)
        assert plan_dir / "plan.md" in files
        assert tmp_path / "README.md" in files
        assert any(".vectora" in v for v in visited), (
            "walk não desceu em .vectora — exceção de estado não aplicada"
        )

    def test_outro_dir_gitignorado_continua_sendo_podado(self, tmp_path):
        """A exceção é só pro `.vectora` — outros dirs gitignorados seguem
        bloqueados normalmente."""
        from backend.services.ignore import is_ignored, load_ignore_spec

        (tmp_path / ".gitignore").write_text("generated/\n")
        spec = load_ignore_spec(tmp_path)

        assert is_ignored(tmp_path / "generated" / "out.md", tmp_path, spec) is True

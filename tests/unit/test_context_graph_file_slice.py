"""Testes para backend/services/context_graph/file_slice.py.

Cobre: FileSlice, unit_path, is_splittable_text, _best_cut,
slice_boundaries, expand_oversized_files, read_slice_text, bisect_slice.
"""

from __future__ import annotations

from pathlib import Path


class TestUnitPath:
    def test_path_returns_itself(self):
        from backend.context_graph.file_slice import unit_path

        p = Path("file.py")
        assert unit_path(p) == p

    def test_file_slice_returns_path(self, tmp_path: Path):
        from backend.context_graph.file_slice import FileSlice, unit_path

        p = tmp_path / "doc.md"
        fs = FileSlice(path=p, start=0, end=10, index=0, total=1)
        assert unit_path(fs) == p


class TestIsSplittableText:
    def test_md_is_splittable(self, tmp_path: Path):
        from backend.context_graph.file_slice import is_splittable_text

        assert is_splittable_text(Path("README.md"))
        assert is_splittable_text(Path("docs/guide.mdx"))
        assert is_splittable_text(Path("notes.txt"))
        assert is_splittable_text(Path("docs.rst"))
        assert is_splittable_text(Path("file.markdown"))

    def test_code_is_not_splittable(self):
        from backend.context_graph.file_slice import is_splittable_text

        assert not is_splittable_text(Path("main.py"))
        assert not is_splittable_text(Path("index.ts"))
        assert not is_splittable_text(Path("app.go"))

    def test_uppercase_extension(self):
        from backend.context_graph.file_slice import is_splittable_text

        assert is_splittable_text(Path("README.MD"))
        assert is_splittable_text(Path("notes.TXT"))


class TestBestCut:
    def test_cuts_at_heading_boundary(self):
        from backend.context_graph.file_slice import _best_cut

        text = "Intro\n# Section\nContent"
        cut = _best_cut(text, 0, len(text))
        # Should cut just before '#'
        assert text[cut - 1] == "\n"

    def test_cuts_at_blank_line(self):
        from backend.context_graph.file_slice import _best_cut

        text = "Para 1\n\nPara 2"
        cut = _best_cut(text, 0, len(text))
        assert cut > 0

    def test_cuts_at_newline(self):
        from backend.context_graph.file_slice import _best_cut

        text = "Line1\nLine2"
        cut = _best_cut(text, 0, len(text))
        assert cut > 0

    def test_fallback_hard_cut(self):
        from backend.context_graph.file_slice import _best_cut

        text = "NoNewlinesHere"
        cut = _best_cut(text, 0, len(text))
        assert cut == len(text)


class TestSliceBoundaries:
    def test_no_split_needed(self):
        from backend.context_graph.file_slice import slice_boundaries

        text = "Short text"
        bounds = slice_boundaries(text, max_chars=100)
        assert bounds == [(0, len(text))]

    def test_splits_into_parts(self):
        from backend.context_graph.file_slice import slice_boundaries

        text = "A" * 100 + "\n" + "B" * 100
        bounds = slice_boundaries(text, max_chars=110)
        assert len(bounds) >= 2
        # Bounds must be contiguous
        for i in range(1, len(bounds)):
            assert bounds[i][0] == bounds[i - 1][1]

    def test_covers_entire_text(self):
        from backend.context_graph.file_slice import slice_boundaries

        text = "Section1\n\nSection2\n\nSection3" * 5
        bounds = slice_boundaries(text, max_chars=30)
        assert bounds[0][0] == 0
        assert bounds[-1][1] == len(text)

    def test_each_slice_within_limit(self):
        from backend.context_graph.file_slice import slice_boundaries

        text = "x" * 200
        max_chars = 50
        bounds = slice_boundaries(text, max_chars=max_chars)
        for start, end in bounds:
            assert end - start <= max_chars


class TestExpandOversizedFiles:
    def test_small_file_passthrough(self, tmp_path: Path):
        from backend.context_graph.file_slice import expand_oversized_files

        f = tmp_path / "small.md"
        f.write_text("Short content", encoding="utf-8")
        result = expand_oversized_files([f], max_chars=1000)
        assert result == [f]

    def test_non_splittable_passthrough(self, tmp_path: Path):
        from backend.context_graph.file_slice import expand_oversized_files

        f = tmp_path / "main.py"
        f.write_text("x" * 5000, encoding="utf-8")
        result = expand_oversized_files([f], max_chars=100)
        assert result == [f]

    def test_oversized_md_split_into_slices(self, tmp_path: Path):
        from backend.context_graph.file_slice import (
            FileSlice,
            expand_oversized_files,
        )

        content = "Section\n\n" + "x" * 200 + "\n\n" + "y" * 200
        f = tmp_path / "big.md"
        f.write_text(content, encoding="utf-8")
        result = expand_oversized_files([f], max_chars=250)
        assert len(result) >= 2
        assert all(isinstance(s, FileSlice) for s in result)
        # All slices refer to the same file
        slices = [s for s in result if isinstance(s, FileSlice)]
        assert all(s.path == f for s in slices)

    def test_unreadable_file_passthrough(self, tmp_path: Path):
        from backend.context_graph.file_slice import expand_oversized_files

        f = tmp_path / "ghost.md"
        # File doesn't exist → OSError → pass through
        result = expand_oversized_files([f], max_chars=10)
        assert result == [f]


class TestReadSliceText:
    def test_reads_correct_range(self, tmp_path: Path):
        from backend.context_graph.file_slice import FileSlice, read_slice_text

        f = tmp_path / "doc.md"
        f.write_text("ABCDEFGHIJ", encoding="utf-8")
        fs = FileSlice(path=f, start=2, end=5, index=0, total=1)
        assert read_slice_text(fs) == "CDE"


class TestBisectSlice:
    def test_bisects_slice(self, tmp_path: Path):
        from backend.context_graph.file_slice import FileSlice, bisect_slice

        content = "A" * 40 + "\n" + "B" * 40
        f = tmp_path / "doc.md"
        f.write_text(content, encoding="utf-8")
        fs = FileSlice(path=f, start=0, end=len(content), index=0, total=1)
        result = bisect_slice(fs)
        assert result is not None
        left, right = result
        assert left.start == 0
        assert right.end == len(content)
        assert left.end == right.start

    def test_too_small_to_bisect(self, tmp_path: Path):
        from backend.context_graph.file_slice import FileSlice, bisect_slice

        f = tmp_path / "doc.md"
        f.write_text("X", encoding="utf-8")
        fs = FileSlice(path=f, start=0, end=1, index=0, total=1)
        assert bisect_slice(fs) is None

    def test_unreadable_file_returns_none(self, tmp_path: Path):
        from backend.context_graph.file_slice import FileSlice, bisect_slice

        f = tmp_path / "ghost.md"
        fs = FileSlice(path=f, start=0, end=100, index=0, total=1)
        assert bisect_slice(fs) is None

    def test_no_newline_fallback(self, tmp_path: Path):
        from backend.context_graph.file_slice import FileSlice, bisect_slice

        content = "A" * 100
        f = tmp_path / "doc.md"
        f.write_text(content, encoding="utf-8")
        fs = FileSlice(path=f, start=0, end=100, index=0, total=1)
        result = bisect_slice(fs)
        assert result is not None

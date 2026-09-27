from __future__ import annotations

import pytest
from prepare_release_pr_body import current_release_section, prepare_body


def test_keeps_only_the_first_release_section() -> None:
    notes = """:robot: release
---

## [0.2.0](https://example.test/0.2)

### Features

* current ([#1](https://example.test/issues/1))

## [0.1.23](https://example.test/0.1)

* historical
"""

    body = prepare_body(notes)

    assert "0.2.0" in body
    assert "current" in body
    assert "historical" not in body


def test_compacts_links_when_current_section_is_too_large() -> None:
    notes = "## [0.2.0](https://example.test/release)\n\n" + "\n".join(
        f"* change {index} ([commit](https://example.test/commits/{index}))"
        for index in range(100)
    )

    body = prepare_body(notes, max_body_bytes=2_500)

    assert "change 99" in body
    assert "https://example.test/release" in body
    assert "https://example.test/commits/" not in body
    assert len(body.encode("utf-8")) <= 2_500


def test_rejects_notes_without_a_release_heading() -> None:
    with pytest.raises(ValueError, match="release heading"):
        current_release_section("no release here")


def test_drops_changelog_document_header() -> None:
    body = prepare_body("# Changelog\n\n## [0.2.0]\n\n### Features\n\n* current\n")

    assert body.startswith(":robot: I have created a release")
    assert "## [0.2.0]" in body
    assert "# Changelog" not in body
    assert "This PR was generated with [Release Please]" in body

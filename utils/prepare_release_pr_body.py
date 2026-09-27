"""Prepara o corpo de uma pull request com apenas a release atual."""

from __future__ import annotations

import re
import sys
from pathlib import Path

MAX_BODY_BYTES = 60_000
RELEASE_PREAMBLE = ":robot: I have created a release *beep* *boop*"
RELEASE_FOOTER = (
    "---\n"
    "This PR was generated with [Release Please](https://github.com/googleapis/release-please). "
    "See [documentation](https://github.com/googleapis/release-please#release-please)."
)
RELEASE_HEADING = re.compile(r"^## \[[^\]]+\].*$", re.MULTILINE)
NEXT_RELEASE_HEADING = re.compile(r"^## \[[^\]]+\].*$", re.MULTILINE)
MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def current_release_section(notes: str) -> tuple[str, str, str]:
    """Retorna o preâmbulo, a primeira seção de release e o rodapé."""
    match = RELEASE_HEADING.search(notes)
    if match is None:
        raise ValueError("release notes do not contain a release heading")
    next_match = NEXT_RELEASE_HEADING.search(notes, match.end())
    end = next_match.start() if next_match else len(notes)
    # A second release heading starts the historical portion.  It must not be
    # copied into the PR body; the current section is the only release context
    # that Release Please should publish.
    prefix = notes[: match.start()].strip()
    if prefix.startswith("# Changelog"):
        prefix = ""
    return prefix, notes[match.start() : end].strip(), ""


def _compact_links(section: str) -> str:
    """Remove URLs repetitivas sem remover os títulos e referências visíveis."""
    heading, separator, remainder = section.partition("\n")
    compacted_remainder = MARKDOWN_LINK.sub(r"\1", remainder)
    return heading + separator + compacted_remainder


def prepare_body(notes: str, max_body_bytes: int = MAX_BODY_BYTES) -> str:
    """Monta um corpo com a seção atual e mantém-o dentro do limite do GitHub."""
    prefix, section, suffix = current_release_section(notes)
    if not prefix:
        prefix = RELEASE_PREAMBLE
    if not suffix:
        suffix = RELEASE_FOOTER

    def join_parts(current_section: str) -> str:
        return (
            "\n\n".join(part for part in (prefix, current_section, suffix) if part)
            + "\n"
        )

    body = join_parts(section)
    if len(body.encode("utf-8")) <= max_body_bytes:
        return body

    compacted = join_parts(_compact_links(section))
    if len(compacted.encode("utf-8")) > max_body_bytes:
        raise ValueError(
            "the current release section exceeds the GitHub pull-request body limit "
            "even after compacting links"
        )
    return compacted


def main() -> int:
    """Lê as notas e grava o corpo pronto para ``gh pr edit --body-file``."""
    if len(sys.argv) != 3:
        print("usage: prepare_release_pr_body.py NOTES OUTPUT", file=sys.stderr)
        return 2
    try:
        raw_notes = Path(sys.argv[1]).read_bytes()
        try:
            notes = raw_notes.decode("utf-8")
        except UnicodeDecodeError:
            # Older generated notes can contain Portuguese text written using
            # the Windows code page.  Normalize that input to UTF-8 on output.
            notes = raw_notes.decode("cp1252")
        Path(sys.argv[2]).write_text(prepare_body(notes), encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"invalid release notes: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

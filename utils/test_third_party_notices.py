"""Contrato do gerador de THIRD_PARTY_NOTICES.md."""

from __future__ import annotations

import pytest
from third_party_notices import classify, normalize_license, render

PYTHON = [
    {"Name": "httpx", "Version": "0.28.1", "License": "BSD-3-Clause", "URL": "u"},
    {"Name": "psycopg", "Version": "3.3.6", "License": "LGPL-3.0-only", "URL": "u"},
    {"Name": "weird", "Version": "1.0", "License": "UNKNOWN", "URL": ""},
]
NPM = {
    "MIT": [{"name": "react", "versions": ["19.0.0"], "homepage": "h"}],
    "FSL-1.1-Apache-2.0": [{"name": "sentry", "versions": ["0.45.0"], "homepage": ""}],
}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MIT License", "MIT"),
        ("Apache Software License", "Apache-2.0"),
        ("BSD License", "BSD"),
        ("GNU Lesser General Public License v3 (LGPLv3)", "LGPL-3.0"),
        ("GNU General Public License v3 (GPLv3)", "GPL-3.0"),
        ("(MIT OR CC0-1.0)", "(MIT OR CC0-1.0)"),
        ("MIT License\n\nCopyright (c) 2022 Autor", "MIT"),
        ("Apache Software License; BSD License", "Apache-2.0 AND BSD"),
        ("DFSG approved; MIT License", "MIT"),
    ],
)
def test_normalize_license_maps_trove_names(raw, expected):
    assert normalize_license(raw) == expected


@pytest.mark.parametrize(
    ("license_id", "category"),
    [
        ("MIT", "permissive"),
        ("BSD-3-Clause", "permissive"),
        ("Apache-2.0", "permissive"),
        ("LGPL-3.0-only", "weak-copyleft"),
        ("MPL-2.0", "weak-copyleft"),
        ("GPL-3.0", "strong-copyleft"),
        ("AGPL-3.0", "strong-copyleft"),
        ("GPL-2.0-only", "review"),
        ("FSL-1.1-Apache-2.0", "review"),
        ("UNKNOWN", "review"),
        ("Qualquer Coisa", "review"),
        ("Apache-2.0 WITH LLVM-exception", "permissive"),
        ("(Apache-2.0 WITH LLVM-exception)", "permissive"),
        ("EPL-2.0 OR GPL-2.0-or-later", "weak-copyleft"),
        ("WTFPL OR ISC", "permissive"),
        ("MIT AND GPL-3.0", "strong-copyleft"),
        ("Apache-2.0 AND BSD", "permissive"),
        ("BSD AND Public Domain", "permissive"),
        ("CC-BY-4.0", "permissive"),
    ],
)
def test_classify_flags_what_needs_human_review(license_id, category):
    assert classify(license_id) == category


def test_render_lists_every_package_and_flags_review_items():
    text = render(PYTHON, NPM)

    assert "| httpx | 0.28.1 | BSD-3-Clause |" in text
    assert "| react | 19.0.0 | MIT |" in text
    review = text.split("## Requer revisão", 1)[1].split("\n## ", 1)[0]
    assert "weird" in review
    assert "sentry" in review
    assert "httpx" not in review


def test_render_keeps_the_attribution_of_incorporated_code():
    text = render(PYTHON, NPM)

    assert "## Atribuição de código incorporado" in text
    assert "Copyright (c) 2024 LangChain" in text


def test_render_is_deterministic_and_sorted():
    assert render(PYTHON, NPM) == render(list(reversed(PYTHON)), NPM)
    text = render(PYTHON, NPM)
    assert text.index("| httpx ") < text.index("| react ")


def test_render_rejects_malformed_input():
    with pytest.raises(ValueError, match="inválid"):
        render([{"Name": "sem-licenca"}], {})
    with pytest.raises(ValueError, match="inválid"):
        render([], {"MIT": [{"versions": ["1"]}]})

"""Testes de utils/unused_code.py: rotas sem navegação e baseline do vulture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import unused_code
from unused_code import (
    _string_literals_py,
    _string_literals_ts,
    compare_baseline,
    literal_targets_route,
    load_entrypoints,
    load_routes,
    parse_vulture,
    route_pattern,
    unreachable_routes,
)


def _write(root: Path, *names: str) -> None:
    for name in names:
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("export const Route = {}", encoding="utf-8")


def test_load_routes_segue_as_convencoes_do_router(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "__root.tsx",
        "index.tsx",
        "onboarding.tsx",
        "session/$threadId.tsx",
        "integrations/index.tsx",
        "auth/signin.tsx",
        "settings.profile.tsx",
        "_layout.tsx",
        "_layout/home.tsx",
        "session/-helpers.ts",
        "(grupo)/info.tsx",
        "auth/__tests__/signin.test.tsx",
    )

    assert sorted(load_routes(tmp_path)) == [
        "/",
        "/auth/signin",
        "/home",
        "/info",
        "/integrations/",
        "/onboarding",
        "/session/$threadId",
        "/settings/profile",
    ]


def test_load_routes_do_repositorio_inclui_as_paginas_reais() -> None:
    routes = load_routes()

    assert "/" in routes
    assert "/session/$threadId" in routes
    assert all(not route.startswith("/__") for route in routes)


@pytest.mark.parametrize(
    ("literal", "route", "expected"),
    [
        ("/onboarding", "/onboarding", True),
        ("/onboarding?step=2", "/onboarding", True),
        ("/session/new", "/session/$threadId", True),
        ("/session/${id}", "/session/$threadId", True),
        ("/session/$threadId", "/session/$threadId", True),
        ("/session/", "/session/$threadId", True),
        ("/integrations", "/integrations/", True),
        ("/integrations/x/verify", "/integrations/", False),
        ("/onboarding/extra", "/onboarding", False),
        ("/", "/", True),
        ("/", "/onboarding", False),
    ],
)
def test_literal_targets_route(literal: str, route: str, expected: bool) -> None:
    assert literal_targets_route(literal, route) is expected


def test_route_pattern_param_nao_atravessa_barra() -> None:
    assert route_pattern("/share/$token").match("/share/abc")
    assert not route_pattern("/share/$token").match("/share/abc/def")


def test_literais_ts_ignoram_comentarios_e_mantem_crase() -> None:
    source = """
    // navigate({ to: "/comentado" })
    /* to: "/bloco" */
    const a = { to: "/real" };
    router.replace(`/session/${id}`);
    const url = "http://x.test/y";
    """

    literals = [literal for literal, _ in _string_literals_ts(source)]

    assert "/real" in literals
    assert "/session/${id}" in literals
    assert "/comentado" not in literals
    assert "/bloco" not in literals
    assert "http://x.test/y" in literals


def test_literais_ts_trazem_o_contexto_anterior() -> None:
    source = 'navigate({ to: "/onboarding" }); fetch("/integrations");'

    contexts = dict(_string_literals_ts(source))

    assert contexts["/onboarding"].rstrip().endswith("to:")
    assert contexts["/integrations"].rstrip().endswith("fetch(")


def test_literais_py_ignoram_comentarios() -> None:
    source = 'x = RedirectResponse(url="/a")  # "/comentado"\n'

    literals = [literal for literal, _ in _string_literals_py(source)]

    assert literals == ["/a"]


def test_unreachable_routes_aponta_sem_navegacao_e_sem_entrada() -> None:
    routes = ["/", "/a", "/b"]
    references = {"/": ["x.tsx"], "/a": [], "/b": []}

    unreachable, stale = unreachable_routes(routes, references, {"/b": "motivo"})

    assert unreachable == ["/a"]
    assert stale == []


def test_entrada_declarada_com_navegacao_ou_inexistente_e_obsoleta() -> None:
    routes = ["/a", "/b"]
    references = {"/a": ["x.tsx"], "/b": []}

    unreachable, stale = unreachable_routes(
        routes, references, {"/a": "motivo", "/removida": "motivo"}
    )

    assert unreachable == ["/b"]
    assert stale == ["/a", "/removida"]


def test_load_entrypoints_exige_motivo(tmp_path: Path) -> None:
    path = tmp_path / "entry.json"
    path.write_text(json.dumps({"/a": "  "}), encoding="utf-8")

    with pytest.raises(ValueError, match="motivo"):
        load_entrypoints(path)

    path.write_text(json.dumps({"/a": "link externo"}), encoding="utf-8")
    assert load_entrypoints(path) == {"/a": "link externo"}


def test_find_route_references_conta_navegacao_e_nao_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(unused_code, "ROOT", tmp_path)
    source = tmp_path / "components"
    source.mkdir()
    (source / "nav.tsx").write_text(
        'navigate({ to: "/onboarding" });\nfetch("/integrations");\n'
        '// router.push("/comentada")\n',
        encoding="utf-8",
    )
    (source / "legacy.test.tsx").write_text('navigate({ to: "/a" })', encoding="utf-8")
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "oauth.py").write_text(
        'return RedirectResponse(url="/integrations")\n', encoding="utf-8"
    )

    found = unused_code.find_route_references(
        ["/onboarding", "/integrations/", "/comentada", "/a"], [source], [backend]
    )

    assert found["/onboarding"] == ["components/nav.tsx"]
    assert found["/integrations/"] == ["backend/oauth.py"]
    assert found["/comentada"] == []
    assert found["/a"] == []


def test_parse_vulture_usa_chave_sem_linha_e_ignora_outros_tipos() -> None:
    output = (
        "backend/a.py:10: unused function 'f' (60% confidence)\n"
        "backend/a.py:99: unused function 'f' (60% confidence)\n"
        "backend/b.py:3: unused method 'm' (60% confidence)\n"
        "backend/b.py:4: unused variable 'v' (60% confidence)\n"
        "backend/c.py:1: unused class 'C' (60% confidence)\n"
    )

    assert parse_vulture(output) == [
        "backend/a.py::function::f",
        "backend/b.py::method::m",
        "backend/c.py::class::C",
    ]


def test_compare_baseline_separa_novos_e_obsoletos() -> None:
    new, gone = compare_baseline(["a", "b"], ["b", "c"])

    assert (new, gone) == (["a"], ["c"])

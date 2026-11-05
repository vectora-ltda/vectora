"""Derivação e unicidade de username (identidade do app, sem email)."""

from __future__ import annotations

import re

from backend.rbac.username import slugify_username, unique_username


def test_slug_simples():
    assert slugify_username("Bruno") == "bruno"


def test_slug_remove_espacos_e_acentos():
    assert slugify_username("José da Silva") == "josedasilva"
    assert slugify_username("Ada Lovelace") == "adalovelace"


def test_slug_mantem_numeros():
    assert slugify_username("Agent 007") == "agent007"


def test_slug_vazio_cai_em_user():
    assert slugify_username("") == "user"
    assert slugify_username("   ") == "user"
    # Só símbolos, nada aproveitável.
    assert slugify_username("@#$%") == "user"


def test_unico_quando_livre_devolve_o_slug():
    assert unique_username("Bruno", lambda _u: False) == "bruno"


def test_colisao_gera_sufixo_de_4_digitos():
    # "bruno" tomado → deve virar "bruno#NNNN".
    taken = {"bruno"}
    out = unique_username("Bruno", lambda u: u in taken)
    assert out != "bruno"
    assert re.fullmatch(r"bruno#\d{4}", out), out


def test_colisao_pula_sufixos_ocupados():
    # base e um sufixo específico tomados → ainda acha um livre no formato.
    taken = {"bruno"}
    out = unique_username("Bruno", lambda u: u in taken)
    assert out not in taken
    assert out.startswith("bruno#")

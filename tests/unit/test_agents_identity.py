"""Tests for src/agents/_identity.py"""

from __future__ import annotations

import pytest

from backend.agents._identity import VECTORA_IDENTITY, detect_system_language

# ---------------------------------------------------------------------------
# VECTORA_IDENTITY (constante)
# ---------------------------------------------------------------------------


def test_identity_is_string():
    assert isinstance(VECTORA_IDENTITY, str)
    assert len(VECTORA_IDENTITY) > 200


def test_identity_contains_stack():
    assert "Native engine" in VECTORA_IDENTITY
    assert "LanceDB" in VECTORA_IDENTITY


def test_identity_licenca_so_quando_perguntado():
    """Vectora é comercial self-hosted, mas a regra é CONDICIONAL: só fala de
    licença/open-source quando perguntado — nunca anuncia proativamente (senão
    responde "não sou open source" a um simples "oi").
    """
    lowered = VECTORA_IDENTITY.lower()
    # Normaliza quebras de linha do blockquote markdown antes de checar frases
    # que podem estar wrapadas em linhas diferentes.
    flat = " ".join(lowered.split())
    # Sabe que é comercial/self-hosted, sem reivindicar licença OSS.
    assert "commercial" in lowered
    assert "self-hosted" in lowered or "self hosted" in lowered
    assert "apache" not in lowered
    # Regra de comportamento: só esclarecer licença quando o usuário perguntar.
    assert "when the user asks" in flat
    # Erro/borda: não pode haver afirmação proativa "isn't open source" (a versão
    # antiga liderava com isso e o agente passou a anunciar num "oi").
    assert "is not open source" not in flat


def test_identity_conhece_workbench_tarefas():
    """A identidade conhece a workbench de Tarefas (tasks, antiga "Segundo plano")."""
    lowered = VECTORA_IDENTITY.lower()
    assert "tasks" in lowered
    assert "background" in lowered  # descreve a funcionalidade
    assert "heartbeat" in lowered
    assert "webhook" in lowered


def test_identity_mentions_cohere():
    assert "Cohere" in VECTORA_IDENTITY


def test_identity_describes_agents():
    assert "Orchestrator" in VECTORA_IDENTITY
    assert "SOUL" in VECTORA_IDENTITY
    assert "task()" in VECTORA_IDENTITY

    # Erro/borda: a identidade não pode voltar a afirmar que tool restriction
    # é só sugestão de prompt — isso já foi falso uma vez nesta mesma seção
    # (investigação real confirmou bind de function-calling como enforcement).
    assert "not from tool restriction" not in VECTORA_IDENTITY


# ---------------------------------------------------------------------------
# detect_system_language — locale do SO, cru
# ---------------------------------------------------------------------------
#
# Fixture limpa as 3 envs de locale antes de cada teste. Cada caso seta
# explicitamente o que precisa. Sem isso, o locale da máquina onde a suite
# roda contamina as expectativas.


@pytest.fixture
def clean_locale_env(monkeypatch):
    """Remove LC_ALL / LC_MESSAGES / LANG do ambiente do teste."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


class TestDetectSystemLanguage:
    def test_returns_lc_all_when_set(self, clean_locale_env):
        clean_locale_env.setenv("LC_ALL", "pt_BR.UTF-8")
        assert detect_system_language() == "pt_BR"

    def test_returns_lang_when_lc_all_absent(self, clean_locale_env):
        clean_locale_env.setenv("LANG", "en_US.UTF-8")
        assert detect_system_language() == "en_US"

    def test_lc_all_has_priority_over_lang(self, clean_locale_env):
        """LC_ALL vence LANG (semântica POSIX)."""
        clean_locale_env.setenv("LC_ALL", "es_ES.UTF-8")
        clean_locale_env.setenv("LANG", "pt_BR.UTF-8")
        assert detect_system_language() == "es_ES"

    def test_lc_messages_between_lc_all_and_lang(self, clean_locale_env):
        """LC_MESSAGES > LANG quando LC_ALL ausente."""
        clean_locale_env.setenv("LC_MESSAGES", "fr_FR.UTF-8")
        clean_locale_env.setenv("LANG", "pt_BR.UTF-8")
        assert detect_system_language() == "fr_FR"

    def test_drops_encoding_suffix(self, clean_locale_env):
        """`.UTF-8`, `.cp1252` etc. são descartados — só o sufixo de encoding."""
        clean_locale_env.setenv("LC_ALL", "pt_BR.UTF-8")
        assert detect_system_language() == "pt_BR"

        clean_locale_env.delenv("LC_ALL")
        clean_locale_env.setenv("LANG", "es_AR.ISO8859-1")
        assert detect_system_language() == "es_AR"

    def test_no_normalization_of_format(self, clean_locale_env):
        """O formato é repassado cru — hífen vs underscore, código longo etc."""
        clean_locale_env.setenv("LC_ALL", "es-419")
        assert detect_system_language() == "es-419"

        clean_locale_env.delenv("LC_ALL")
        clean_locale_env.setenv("LANG", "pt-br")
        assert detect_system_language() == "pt-br"

    def test_ignores_c_and_posix_locales(self, clean_locale_env):
        """`C` e `POSIX` são locales "neutros" — equivalem a "sem preferência"."""
        clean_locale_env.setenv("LC_ALL", "C")
        # Sem fallback do SO, deve devolver vazio. Mocka getdefaultlocale para
        # garantir resposta vazia mesmo num ambiente que tenha locale do SO.
        import backend.agents._identity as identity_mod

        clean_locale_env.setattr(
            identity_mod, "__name__", identity_mod.__name__
        )  # noop; mantém referência
        # Patch direto no módulo locale para isolar do SO real
        import locale as _locale

        clean_locale_env.setattr(_locale, "getdefaultlocale", lambda: (None, None))
        clean_locale_env.setattr(_locale, "getlocale", lambda *a, **kw: (None, None))
        assert detect_system_language() == ""

        clean_locale_env.setenv("LC_ALL", "POSIX")
        assert detect_system_language() == ""

    def test_returns_empty_when_nothing_set_and_locale_empty(self, clean_locale_env):
        """Sem env vars e sem locale do SO → string vazia."""
        import locale as _locale

        clean_locale_env.setattr(_locale, "getdefaultlocale", lambda: (None, None))
        clean_locale_env.setattr(_locale, "getlocale", lambda *a, **kw: (None, None))
        assert detect_system_language() == ""

    def test_falls_back_to_getdefaultlocale(self, clean_locale_env):
        """Sem env vars, getdefaultlocale do Python decide (caso típico Windows)."""
        import locale as _locale

        clean_locale_env.setattr(
            _locale, "getdefaultlocale", lambda: ("pt_BR", "cp1252")
        )
        assert detect_system_language() == "pt_BR"

    def test_accepts_windows_style_locale(self, clean_locale_env):
        """Locale estilo Windows ('Portuguese_Brazil') é repassado cru."""
        import locale as _locale

        clean_locale_env.setattr(_locale, "getdefaultlocale", lambda: (None, None))
        clean_locale_env.setattr(
            _locale, "getlocale", lambda *a, **kw: ("Portuguese_Brazil", "1252")
        )
        assert detect_system_language() == "Portuguese_Brazil"

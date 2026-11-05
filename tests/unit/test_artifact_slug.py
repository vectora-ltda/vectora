"""
Testes unitários para a função _artifact_slug em backend/tools/fs.py

Garante que títulos de artefatos não sejam truncados independentemente do
comprimento — corrige o bug onde slugs eram limitados a 50 caracteres.
"""

import pytest

from backend.tools.fs import _artifact_slug


class TestArtifactSlug:
    """Testa a conversão de títulos em slugs kebab-case."""

    def test_titulo_curto_preservado(self):
        assert _artifact_slug("Meu Plano") == "meu-plano"

    def test_titulo_longo_nao_truncado(self):
        """Bug fix: títulos com mais de 50 chars não devem ser truncados."""
        longo = "Plano de correção de tratamento de imagens e estabilização de pipeline de video"
        slug = _artifact_slug(longo)
        # Deve conter a palavra final do título (sem acento pois _artifact_slug remove char especiais)
        assert "video" in slug or "vdeo" in slug or "pipeline" in slug
        # Comprimento deve ser maior que 50
        assert len(slug) > 50

    def test_titulo_exatamente_50_chars(self):
        """Título de exatamente 50 chars deve ser preservado integralmente."""
        titulo = "Plano de refatoracao do modulo de autenticacao 99x"
        assert len(titulo) == 50
        slug = _artifact_slug(titulo)
        assert len(slug) == len("plano-de-refatoracao-do-modulo-de-autenticacao-99x")

    def test_titulo_com_acentos(self):
        slug = _artifact_slug("Correção de Erros na API")
        assert (
            "correo" in slug
            or "correo-de-erros" in slug
            or "correo" in slug
            or "erros" in slug
        )

    def test_titulo_com_caracteres_especiais(self):
        slug = _artifact_slug("Plano: Fase #1 (Backend)")
        assert "#" not in slug
        assert "(" not in slug
        assert ")" not in slug
        assert ":" not in slug

    def test_titulo_vazio_retorna_artifact(self):
        assert _artifact_slug("") == "artifact"

    def test_titulo_so_espacos_retorna_artifact(self):
        assert _artifact_slug("   ") == "artifact"

    def test_slug_sem_traco_no_inicio_ou_fim(self):
        slug = _artifact_slug("  -- Plano de Testes --  ")
        assert not slug.startswith("-")
        assert not slug.endswith("-")

    def test_multiplos_espacos_viram_unico_traco(self):
        slug = _artifact_slug("Plano    de    Testes")
        assert "--" not in slug

    def test_titulo_com_underscores(self):
        slug = _artifact_slug("deploy_to_production")
        assert "_" not in slug
        assert "deploy" in slug
        assert "production" in slug

    @pytest.mark.parametrize("length", [51, 100, 150, 200])
    def test_titulo_muito_longo_nao_truncado(self, length: int):
        """Quaisquer títulos longos são preservados por inteiro."""
        titulo = "palavra " * (length // 8)
        slug = _artifact_slug(titulo)
        # O slug deve representar o título completo
        word_count = len(titulo.split())
        slug_word_count = len(slug.split("-"))
        assert slug_word_count >= word_count - 1  # -1 para tolerância de edge

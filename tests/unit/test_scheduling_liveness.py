"""Classificador de liveness semântica — sinaliza padrões de
texto que indicam estagnação numa run de subagente/task, sem nunca pausar
ou bloquear nada sozinho (puramente informativo).
"""

from __future__ import annotations

from backend.scheduling.liveness import classify_liveness


class TestClassifyLiveness:
    def test_blocked_external_reconhece_espera_por_humano(self):
        texto = "Estou aguardando resposta do usuário para continuar."
        assert classify_liveness(texto) == "blocked_external"

    def test_blocked_external_em_ingles(self):
        texto = "Waiting for human approval before proceeding."
        assert classify_liveness(texto) == "blocked_external"

    def test_manager_review_reconhece_pedido_de_revisao(self):
        texto = "Esta mudança precisa de revisão antes de prosseguir."
        assert classify_liveness(texto) == "manager_review"

    def test_planning_only_reconhece_so_plano_sem_execucao(self):
        texto = "Criei um plano detalhado para a implementação."
        assert classify_liveness(texto) == "planning_only"

    def test_texto_sem_padrao_conhecido_devolve_none(self):
        """Caso comum: a run progrediu normalmente, sem nenhum sinal de
        estagnação — não deve dar falso positivo."""
        texto = "Implementei a função, rodei os testes e todos passaram."
        assert classify_liveness(texto) is None

    def test_texto_vazio_devolve_none(self):
        assert classify_liveness("") is None
        assert classify_liveness(None) is None

    def test_case_insensitive(self):
        texto = "AGUARDANDO RESPOSTA DO HUMANO PARA CONTINUAR"
        assert classify_liveness(texto) == "blocked_external"

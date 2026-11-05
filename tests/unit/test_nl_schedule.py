"""Schedule — parser determinístico de linguagem natural pra cron.

Erro/borda: expressão ambígua/vazia/fora dos padrões suportados nunca é
adivinhada — retorna None, e quem chama pede esclarecimento.
"""

from __future__ import annotations

from backend.scheduling.nl_schedule import parse_natural_schedule, parse_one_shot_delay


def test_daily_with_explicit_time():
    assert parse_natural_schedule("todo dia às 9h") == "0 9 * * *"
    assert parse_natural_schedule("todos os dias às 14:30") == "30 14 * * *"


def test_daily_without_time_defaults_to_9am():
    assert parse_natural_schedule("todo dia") == "0 9 * * *"


def test_weekly_on_weekday_with_time():
    assert parse_natural_schedule("toda sexta-feira às 18h") == "0 18 * * 5"
    assert parse_natural_schedule("toda segunda às 8:15") == "15 8 * * 1"


def test_weekly_without_time_defaults_to_9am():
    assert parse_natural_schedule("toda quinta") == "0 9 * * 4"


def test_interval_minutes_and_hours():
    assert parse_natural_schedule("a cada 15 minutos") == "*/15 * * * *"
    assert parse_natural_schedule("a cada 2 horas") == "0 */2 * * *"


def test_empty_string_returns_none_not_a_guess():
    assert parse_natural_schedule("") is None
    assert parse_natural_schedule("   ") is None


def test_ambiguous_expression_returns_none():
    # "daqui 2 horas" é execução única, não recorrente — fora do escopo do
    # parser (cron não expressa "uma vez"); não deve inventar uma
    # recorrência errada.
    assert parse_natural_schedule("daqui 2 horas") is None
    assert parse_natural_schedule("quando der") is None


def test_interval_zero_or_out_of_range_is_rejected():
    assert parse_natural_schedule("a cada 0 minutos") is None
    assert parse_natural_schedule("a cada 60 minutos") is None
    assert parse_natural_schedule("a cada 24 horas") is None


def test_hora_invalida_fora_do_range_0_23_retorna_none():
    # Erro/borda: "25h" não é hora válida — não deve virar cron com valor
    # fora de faixa (isso seria um agendamento silenciosamente errado).
    assert parse_natural_schedule("todo dia às 25h") is None


def test_minuto_invalido_fora_do_range_0_59_retorna_none():
    assert parse_natural_schedule("todo dia às 9:99") is None


def test_variantes_de_acentuacao_do_dia_da_semana_sao_equivalentes():
    # "terça" com cedilha/acento e "terca"/"terca-feira" sem — mesmo
    # resultado, texto de usuário real não é garantido vir acentuado.
    assert parse_natural_schedule("toda terça às 10h") == "0 10 * * 2"
    assert parse_natural_schedule("toda terca às 10h") == "0 10 * * 2"
    assert parse_natural_schedule("toda terca-feira às 10h") == "0 10 * * 2"
    assert parse_natural_schedule("toda sábado às 10h") == "0 10 * * 6"
    assert parse_natural_schedule("toda sabado às 10h") == "0 10 * * 6"


def test_maiusculas_sao_normalizadas_para_minusculas():
    assert parse_natural_schedule("TODO DIA ÀS 9H") == "0 9 * * *"


def test_espacos_nas_pontas_sao_removidos_antes_do_parse():
    assert parse_natural_schedule("   todo dia às 9h   ") == "0 9 * * *"


def test_intervalo_tem_prioridade_sobre_padrao_diario_se_ambos_aparecerem():
    # Ordem de tentativa é every_n -> weekly -> daily; expressão que
    # mistura "a cada" com "todo dia" não deve virar diário sem querer.
    assert parse_natural_schedule("a cada 30 minutos todo dia") == "*/30 * * * *"


def test_intervalo_com_numero_negativo_nao_casa_regex_retorna_none():
    # "-5" não é capturado por \d+ (só dígitos) — cai pra None, não
    # interpreta como "5" positivo por engano.
    assert parse_natural_schedule("a cada -5 minutos") is None


def test_dia_da_semana_sem_padrao_toda_prefixo_nao_e_reconhecido():
    # "segunda às 9h" sem o prefixo "toda"/"todas" não casa o padrão
    # semanal — expressão ambígua demais, retorna None em vez de adivinhar.
    assert parse_natural_schedule("segunda às 9h") is None


def test_unidade_de_intervalo_invalida_retorna_none():
    # "a cada 3 dias" não é uma unidade suportada (só minuto/hora) —
    # regex não casa "dia"/"dias" como unit, cai pra None.
    assert parse_natural_schedule("a cada 3 dias") is None


def test_expressao_so_com_hora_sem_nenhum_padrao_retorna_none():
    # "às 9h" sozinho, sem "todo dia"/"toda X"/"a cada", é ambíguo — não
    # há recorrência implícita a partir de só um horário.
    assert parse_natural_schedule("às 9h") is None


def test_texto_totalmente_nao_relacionado_retorna_none():
    assert parse_natural_schedule("me manda um email amanhã") is None
    assert parse_natural_schedule("configuração inicial do projeto") is None


def test_invalid_hour_in_expression_is_rejected_not_clamped():
    # Erro/borda: "às 25h" não é um horário válido — não deve virar um
    # cron silenciosamente errado (ex.: truncando pra 2h ou 5h).
    assert parse_natural_schedule("todo dia às 25h") is None


def test_invalid_minute_in_expression_is_rejected():
    assert parse_natural_schedule("todo dia às 9:99") is None


def test_case_and_extra_whitespace_are_normalized():
    assert parse_natural_schedule("  TODO DIA às 9H  ") == "0 9 * * *"
    assert parse_natural_schedule("TODA SEXTA-FEIRA às 18h") == "0 18 * * 5"


def test_weekday_hyphen_and_no_hyphen_variants_agree():
    assert parse_natural_schedule("toda terça") == parse_natural_schedule(
        "toda terça-feira"
    )
    assert parse_natural_schedule("toda terca") == parse_natural_schedule("toda terça")


def test_unrelated_natural_language_never_produces_a_guessed_cron():
    # Frases sem nenhum padrão reconhecido — nunca deve inventar uma
    # recorrência a partir de palavras soltas como "hora"/"dia".
    assert parse_natural_schedule("me lembra de tomar água") is None
    assert parse_natural_schedule("depois do almoço") is None
    assert parse_natural_schedule("um dia desses") is None


def test_every_n_pattern_takes_precedence_over_daily_when_both_present():
    # Ambíguo por conter dois padrões simultâneos — o parser é
    # determinístico (ordem fixa de tentativa), não deve alternar
    # aleatoriamente entre interpretações a cada chamada.
    result_1 = parse_natural_schedule("a cada 2 horas todo dia")
    result_2 = parse_natural_schedule("a cada 2 horas todo dia")

    assert result_1 == result_2 == "0 */2 * * *"


def test_only_whitespace_variants_all_return_none():
    assert parse_natural_schedule("\t\n") is None
    assert parse_natural_schedule("     ") is None


# ---------------------------------------------------------------------------
# parse_one_shot_delay
# ---------------------------------------------------------------------------


def test_one_shot_em_n_minutos_retorna_timestamp_futuro():
    from datetime import UTC, datetime, timedelta

    result = parse_one_shot_delay("em 30 minutos")

    assert result is not None
    parsed = datetime.fromisoformat(result)
    now = datetime.now(UTC)
    assert timedelta(minutes=25) < (parsed - now) < timedelta(minutes=35)


def test_one_shot_daqui_n_horas_retorna_timestamp_futuro():
    from datetime import UTC, datetime, timedelta

    result = parse_one_shot_delay("daqui 2 horas")

    assert result is not None
    parsed = datetime.fromisoformat(result)
    now = datetime.now(UTC)
    assert (
        timedelta(hours=1, minutes=55) < (parsed - now) < timedelta(hours=2, minutes=5)
    )


def test_one_shot_daqui_a_n_minutos_variante_com_preposicao():
    result = parse_one_shot_delay("daqui a 10 minutos")
    assert result is not None


def test_one_shot_string_vazia_retorna_none():
    assert parse_one_shot_delay("") is None
    assert parse_one_shot_delay("   ") is None


def test_one_shot_expressao_recorrente_nao_e_reconhecida():
    # "todo dia às 9h" é recorrência, não execução única — não deve
    # produzir um timestamp (o parser certo pra isso é parse_natural_schedule).
    assert parse_one_shot_delay("todo dia às 9h") is None


def test_one_shot_numero_zero_ou_negativo_e_rejeitado():
    assert parse_one_shot_delay("em 0 minutos") is None


def test_one_shot_expressao_nao_relacionada_retorna_none():
    assert parse_one_shot_delay("quando der") is None


# ---------------------------------------------------------------------------
# i18n — inglês e espanhol (mesmos padrões, tentados em sequência com pt-BR)
# ---------------------------------------------------------------------------


def test_daily_em_ingles():
    assert parse_natural_schedule("every day at 9am") == "0 9 * * *"
    assert parse_natural_schedule("every day at 14:30") == "30 14 * * *"
    assert parse_natural_schedule("every day at 2:30pm") == "30 14 * * *"


def test_daily_em_espanhol():
    assert parse_natural_schedule("todos los días a las 9h") == "0 9 * * *"
    assert parse_natural_schedule("todos los dias a las 14:30") == "30 14 * * *"


def test_weekly_em_ingles():
    assert parse_natural_schedule("every monday at 2pm") == "0 14 * * 1"
    assert parse_natural_schedule("every friday") == "0 9 * * 5"


def test_weekly_em_espanhol():
    assert parse_natural_schedule("todos los lunes a las 14h") == "0 14 * * 1"
    assert parse_natural_schedule("todos los sábados") == "0 9 * * 6"
    assert parse_natural_schedule("todos los sabados") == "0 9 * * 6"


def test_interval_em_ingles():
    assert parse_natural_schedule("every 15 minutes") == "*/15 * * * *"
    assert parse_natural_schedule("every 2 hours") == "0 */2 * * *"


def test_interval_em_espanhol():
    assert parse_natural_schedule("cada 15 minutos") == "*/15 * * * *"
    assert parse_natural_schedule("cada 2 horas") == "0 */2 * * *"


def test_expressao_sem_match_em_nenhum_idioma_retorna_none():
    assert parse_natural_schedule("please remind me later") is None
    assert parse_natural_schedule("recuérdame más tarde") is None


def test_one_shot_em_ingles():
    result = parse_one_shot_delay("in 30 minutes")
    assert result is not None


def test_one_shot_em_espanhol():
    result = parse_one_shot_delay("en 30 minutos")
    assert result is not None

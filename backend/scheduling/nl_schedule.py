"""Schedule — parser de linguagem natural (pt/en/es) pra expressão cron.

Determinístico via regex, não um LLM call: cobre os padrões mais comuns
("todo dia às 9h"/"every day at 9am"/"todos los días a las 9h", "toda
segunda às 14:30", "a cada 2 horas") sem depender de uma chamada de modelo
extra nem introduzir não-determinismo nos testes. Os 3 idiomas são
tentados em sequência (independente do idioma corrente da UI — cobre
copiar um prompt de outro idioma). Expressão fora desses padrões retorna
`None` — o caller (`schedule_task`) pede esclarecimento em vez de adivinhar
(nunca cria um agendamento errado silenciosamente).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

_WEEKDAYS_PT = {
    "domingo": 0,
    "segunda": 1,
    "segunda-feira": 1,
    "terça": 2,
    "terca": 2,
    "terça-feira": 2,
    "terca-feira": 2,
    "quarta": 3,
    "quarta-feira": 3,
    "quinta": 4,
    "quinta-feira": 4,
    "sexta": 5,
    "sexta-feira": 5,
    "sábado": 6,
    "sabado": 6,
}

_WEEKDAYS_EN = {
    "sunday": 0,
    "monday": 1,
    "tuesday": 2,
    "wednesday": 3,
    "thursday": 4,
    "friday": 5,
    "saturday": 6,
}

_WEEKDAYS_ES = {
    "domingo": 0,
    "lunes": 1,
    "martes": 2,
    "miércoles": 3,
    "miercoles": 3,
    "jueves": 4,
    "viernes": 5,
    "sábado": 6,
    "sabado": 6,
}

# Horário: aceita "9h", "14:30", "9am", "2:30pm" — [:h] separa hora/minuto,
# am/pm opcional (12h) convertido pra 24h abaixo.
_TIME_RE = r"(\d{1,2})(?:[:h](\d{2}))?\s*(am|pm)?h?"

# (regex do dia da semana, dicionário de dias) por idioma — tentados em
# sequência, não amarrado ao idioma corrente da UI.
_WEEKLY_PATTERNS = (
    (
        (
            r"toda(?:s)?\s+(domingo|segunda-feira|segunda|terça-feira|terça|terca-feira|"
            r"terca|quarta-feira|quarta|quinta-feira|quinta|sexta-feira|sexta|sábado|sabado)"
        ),
        _WEEKDAYS_PT,
    ),
    (
        r"every\s+(sunday|monday|tuesday|wednesday|thursday|friday|saturday)",
        _WEEKDAYS_EN,
    ),
    (
        r"todos\s+los\s+(domingos?|lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bados?)",
        _WEEKDAYS_ES,
    ),
)

# regex "diário" por idioma.
_DAILY_PATTERNS = (
    r"todo(?:s)?\s+(os?\s+)?dias?",
    r"every\s+day",
    r"todos\s+los\s+d[ií]as",
)

# regex "a cada N min/horas" por idioma — grupo 1 = número, grupo 2 =
# unidade (sempre normalizada pro singular em português na leitura abaixo).
_EVERY_N_PATTERNS = (
    (r"a cada (\d+)\s*(minuto|hora)s?", {"minuto": "minuto", "hora": "hora"}),
    (r"every\s+(\d+)\s*(minute|hour)s?", {"minute": "minuto", "hour": "hora"}),
    (r"cada\s+(\d+)\s*(minuto|hora)s?", {"minuto": "minuto", "hora": "hora"}),
)

# regex "em/daqui N min/horas" (execução única) por idioma.
_ONE_SHOT_PATTERNS = (
    (
        r"(?:em|daqui(?:\s+a)?)\s+(\d+)\s*(minuto|hora)s?",
        {"minuto": "minuto", "hora": "hora"},
    ),
    (r"in\s+(\d+)\s*(minute|hour)s?", {"minute": "minuto", "hour": "hora"}),
    (r"en\s+(\d+)\s*(minuto|hora)s?", {"minuto": "minuto", "hora": "hora"}),
)


class _InvalidTimeError(ValueError):
    """Horário reconhecido na expressão mas fora de faixa (0-23h/0-59min).

    Distinto de "nenhum horário presente" (que usa o default 9h) — um
    horário presente porém inválido não deve ser tratado como ausente,
    senão a expressão vira um agendamento silenciosamente errado."""


def _parse_time(text: str) -> tuple[int, int] | None:
    match = re.search(_TIME_RE, text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise _InvalidTimeError(f"horário fora de faixa: {hour}:{minute}")
    return hour, minute


def _parse_every_n(normalized: str) -> str | None:
    for pattern, unit_map in _EVERY_N_PATTERNS:
        every_n = re.search(pattern, normalized)
        if not every_n:
            continue
        n = int(every_n.group(1))
        unit = unit_map[every_n.group(2)]
        limit = 60 if unit == "minuto" else 24
        if not (0 < n < limit):
            return None
        return f"*/{n} * * * *" if unit == "minuto" else f"0 */{n} * * *"
    return None


def _parse_weekly(normalized: str) -> str | None:
    for pattern, weekdays in _WEEKLY_PATTERNS:
        weekday_match = re.search(pattern, normalized)
        if not weekday_match:
            continue
        token = weekday_match.group(1)
        dow = weekdays.get(token)
        if dow is None:
            # domingos/sábados (ES, plural) — singular não está no dict.
            dow = weekdays.get(token.rstrip("s"))
        if dow is None:
            continue
        try:
            hour, minute = _parse_time(normalized) or (9, 0)
        except _InvalidTimeError:
            # Horário explícito fora de faixa ("toda segunda às 25h") —
            # rejeita a expressão inteira em vez de cair no default 9h.
            return None
        return f"{minute} {hour} * * {dow}"
    return None


def _parse_daily(normalized: str) -> str | None:
    if not any(re.search(pattern, normalized) for pattern in _DAILY_PATTERNS):
        return None
    try:
        hour, minute = _parse_time(normalized) or (9, 0)
    except _InvalidTimeError:
        return None
    return f"{minute} {hour} * * *"


def parse_one_shot_delay(when: str) -> str | None:
    """Converte uma expressão de execução ÚNICA em linguagem natural pra um
    timestamp ISO futuro (UTC) — distinto de ``parse_natural_schedule``, que
    só produz recorrência (cron). Padrões suportados (pt/en/es): "em N
    minutos/horas", "daqui N minutos/horas", "in N minutes/hours", "en N
    minutos/horas". Retorna ``None`` fora desses padrões, incluindo string
    vazia — nunca adivinha.
    """
    normalized = when.strip().lower()
    if not normalized:
        return None

    for pattern, unit_map in _ONE_SHOT_PATTERNS:
        match = re.search(pattern, normalized)
        if not match:
            continue
        n = int(match.group(1))
        unit = unit_map[match.group(2)]
        if n <= 0:
            return None
        delta = timedelta(minutes=n) if unit == "minuto" else timedelta(hours=n)
        return (datetime.now(UTC) + delta).isoformat()
    return None


def parse_natural_schedule(when: str) -> str | None:
    """Converte uma expressão em linguagem natural pra cron de 5 campos.

    Padrões suportados (pt/en/es), tentados em sequência:
    - "todo dia às 9h" / "every day at 9am" / "todos los días a las 9h"
    - "toda segunda às 14h" / "every monday at 2pm" / "todos los lunes"
    - "a cada N minutos/horas" / "every N minutes/hours" / "cada N minutos"

    Retorna `None` (não adivinha) quando a expressão não casa com nenhum
    padrão reconhecido, incluindo string vazia.
    """
    normalized = when.strip().lower()
    if not normalized:
        return None

    for parser in (_parse_every_n, _parse_weekly, _parse_daily):
        try:
            result = parser(normalized)
        except _InvalidTimeError:
            return None
        if result is not None:
            return result
    return None

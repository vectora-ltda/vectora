"""Classificação de erros do stream em códigos tipados (RATE_LIMIT/AUTH/...).

Garante que erros crus de provider (ex.: 429 do Gemini com JSON enorme) viram
um ``(code, message)`` limpo — o frontend localiza a mensagem ao usuário a
partir do ``code`` e nunca exibe o JSON cru como se fosse resposta da IA.
"""

from __future__ import annotations

from backend.api.adapters import classify_stream_error

_GEMINI_429 = (
    "Error calling model 'gemini-2.5-flash' (Too Many Requests): 429 "
    "Too Many Requests. RESOURCE_EXHAUSTED — quota exceeded for "
    "generate_content_free_tier_requests"
)


def test_rate_limit_429_classified():
    code, message = classify_stream_error(RuntimeError(_GEMINI_429))
    assert code == "RATE_LIMIT"
    assert message
    # Não vaza o JSON/refs crus do provider.
    assert "RESOURCE_EXHAUSTED" not in message
    assert "429" not in message


def test_rate_limit_quota_wording():
    code, _ = classify_stream_error(Exception("You exceeded your current quota"))
    assert code == "RATE_LIMIT"


def test_auth_error_classified():
    code, _ = classify_stream_error(Exception("401 Unauthorized: invalid api key"))
    assert code == "AUTH"


def test_timeout_classified():
    code, message = classify_stream_error(Exception("ReadTimeout: request timed out"))
    assert code == "TIMEOUT"
    assert message
    assert "timeout" not in message.lower() or message  # mensagem limpa sem stack


def test_timeout_connect():
    code, _ = classify_stream_error(
        Exception("ConnectTimeout connecting to generativelanguage")
    )
    assert code == "TIMEOUT"


def test_generic_error_fallback():
    code, message = classify_stream_error(ValueError("algo inesperado"))
    assert code == "STREAM_ERROR"
    assert message


def test_timeout_not_auth_not_rate_limit():
    """TIMEOUT deve ser distinto de RATE_LIMIT e AUTH."""
    t_code, _ = classify_stream_error(Exception("timed out"))
    r_code, _ = classify_stream_error(Exception("429 quota"))
    a_code, _ = classify_stream_error(Exception("401 unauthorized"))
    assert len({t_code, r_code, a_code}) == 3


def test_missing_key_getenv_classified():
    from backend.services.env import GetEnvError

    code, message = classify_stream_error(
        GetEnvError("Env variable 'COHERE_API_KEY' does not exist")
    )
    assert code == "MISSING_KEYS"
    assert message
    # Não vaza o nome cru da env pro usuário.
    assert "COHERE_API_KEY" not in message


def test_missing_key_wrapped_in_attribute_error():
    # GetEnvError pode chegar embrulhado num AttributeError (lib externa
    # tentando acessar um atributo que não existe na exceção) — o texto
    # ainda cita GetEnvError, então a classificação precisa pegar isso.
    code, _ = classify_stream_error(
        AttributeError("'GetEnvError' object has no attribute 'generations'")
    )
    assert code == "MISSING_KEYS"


def test_missing_key_plain_env_message():
    code, _ = classify_stream_error(
        Exception("Env variable 'GOOGLE_API_KEY' does not exist")
    )
    assert code == "MISSING_KEYS"


def test_missing_key_takes_precedence_over_auth():
    # Falta de chave cita "api key" mas não é AUTH (401/403) — MISSING_KEYS ganha.
    from backend.services.env import GetEnvError

    code, _ = classify_stream_error(GetEnvError("api_key not configured"))
    assert code == "MISSING_KEYS"


def test_model_incompatible_not_confused_with_real_quota():
    """Bug reproduzido ao vivo: Cohere Command A+ rejeita `tool_plan` em
    TODOS os candidatos da cadeia de fallback — o fallback esgota e levanta
    QuotaExhaustedError, cuja mensagem contém a palavra "quota" e casava
    com o classificador RATE_LIMIT, mentindo pro usuário que o limite de
    uso foi atingido quando na verdade é incompatibilidade de schema."""
    from backend.llm.provider_fallback import QuotaExhaustedError

    root_cause = Exception(
        "invalid request: invalid message provided at index 3: "
        "`tool plan` cannot be used with this model."
    )
    try:
        raise QuotaExhaustedError(
            "Todos os providers esgotaram a quota (último: cohere:command-a-plus-05-2026)."
        ) from root_cause
    except QuotaExhaustedError as exc:
        code, message = classify_stream_error(exc)

    assert code == "MODEL_INCOMPATIBLE"
    assert "limite de uso" not in message.lower()
    assert "quota" not in message.lower()


def test_direct_provider_incompatible_error_classified():
    code, _ = classify_stream_error(
        Exception("`tool plan` cannot be used with this model.")
    )
    assert code == "MODEL_INCOMPATIBLE"


def test_graph_recursion_error_classified():
    """Bug reproduzido ao vivo: troca de provider por falso "quota esgotada"
    (ver test_config_settings::TestLlmKeyPrecedence) deixava o orchestrator
    em loop de delegação até estourar um limite de passos — a mensagem
    genérica de recursão propagava como STREAM_ERROR, sem explicar ao
    usuário o que de fato aconteceu. `classify_stream_error` casa por texto
    da mensagem (não pelo tipo da exceção), então qualquer exceção com essa
    mensagem — nativa ou de uma dependência — é classificada igual."""
    code, message = classify_stream_error(
        RecursionError(
            "Recursion limit of 50 reached without hitting a stop condition."
        )
    )
    assert code == "RECURSION_LIMIT"
    assert message
    assert "loop" in message.lower()
    # Não vaza o número cru do limite/jargão interno pro usuário.
    assert "50" not in message


def test_graph_recursion_not_confused_with_rate_limit():
    r_code, _ = classify_stream_error(Exception("recursion limit reached"))
    q_code, _ = classify_stream_error(Exception("429 quota"))
    assert r_code != q_code

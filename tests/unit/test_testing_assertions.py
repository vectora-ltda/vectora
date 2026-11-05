"""Testes para backend/testing/assertions.py — sobre VMessage nativo. Este
módulo nunca foi exercitado por nenhum teste da suíte (zero import de
`backend.testing` em `tests/`) — reconectar aqui fecha essa lacuna."""

from __future__ import annotations

import pytest

from backend.testing.assertions import (
    assert_last_message_is_ai,
    assert_message_contains_text,
    assert_tool_called,
    assert_tool_called_with_args,
    assert_tool_result_in_messages,
)
from backend.testing.message_factory import (
    make_assistant_message,
    make_tool_result,
    make_user_message,
)
from backend.vtypes.message import ToolCall


class TestAssertToolCalled:
    def test_encontra_tool_chamada(self):
        msgs = [
            make_user_message("oi"),
            make_assistant_message(
                "", tool_calls=[ToolCall(id="1", name="buscar", args={})]
            ),
        ]
        assert_tool_called(msgs, "buscar")

    def test_tool_nao_chamada_levanta_assertion_error(self):
        msgs = [make_user_message("oi"), make_assistant_message("resposta")]
        with pytest.raises(AssertionError, match="was not called"):
            assert_tool_called(msgs, "buscar")


class TestAssertToolCalledWithArgs:
    def test_args_batem(self):
        msgs = [
            make_assistant_message(
                "", tool_calls=[ToolCall(id="1", name="buscar", args={"q": "x"})]
            )
        ]
        assert_tool_called_with_args(msgs, "buscar", {"q": "x"})

    def test_args_diferentes_levanta_assertion_error(self):
        msgs = [
            make_assistant_message(
                "", tool_calls=[ToolCall(id="1", name="buscar", args={"q": "x"})]
            )
        ]
        with pytest.raises(AssertionError):
            assert_tool_called_with_args(msgs, "buscar", {"q": "y"})

    def test_tool_ausente_levanta_assertion_error(self):
        msgs = [make_assistant_message("sem tool calls")]
        with pytest.raises(AssertionError):
            assert_tool_called_with_args(msgs, "buscar", {"q": "x"})


class TestAssertToolResultInMessages:
    def test_encontra_resultado(self):
        msgs = [make_tool_result("call-1", "42")]
        msgs[0].name = "somar"
        assert_tool_result_in_messages(msgs, "somar", 42)

    def test_resultado_ausente_levanta_assertion_error(self):
        msgs = [make_tool_result("call-1", "outro valor")]
        msgs[0].name = "somar"
        with pytest.raises(AssertionError):
            assert_tool_result_in_messages(msgs, "somar", 42)


class TestAssertMessageContainsText:
    def test_encontra_texto(self):
        msgs = [make_user_message("olá mundo")]
        assert_message_contains_text(msgs, "mundo")

    def test_texto_ausente_levanta_assertion_error(self):
        msgs = [make_user_message("olá mundo")]
        with pytest.raises(AssertionError):
            assert_message_contains_text(msgs, "adeus")


class TestAssertLastMessageIsAi:
    def test_ultima_mensagem_e_assistant(self):
        msgs = [make_user_message("oi"), make_assistant_message("resposta")]
        resultado = assert_last_message_is_ai(msgs)
        assert resultado.text() == "resposta"

    def test_lista_vazia_levanta_assertion_error(self):
        with pytest.raises(AssertionError):
            assert_last_message_is_ai([])

    def test_ultima_mensagem_nao_e_assistant_levanta_assertion_error(self):
        msgs = [make_assistant_message("resposta"), make_user_message("outra")]
        with pytest.raises(AssertionError):
            assert_last_message_is_ai(msgs)

"""Tests — descoberta dinâmica de portas em src/main.py.

Garante que o servidor chat standalone não use portas fortemente tipadas:
- _find_free_port() sem preferência retorna uma porta livre utilizável.
- _find_free_port(preferred) retorna a preferida quando livre.
- _find_free_port(preferred) cai para porta efêmera quando a preferida está
  ocupada (evita colisão fixa entre frontend e API interna).
"""

from __future__ import annotations

import socket

from backend.main import _find_free_port


def _is_bindable(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class TestFindFreePort:
    def test_returns_valid_free_port_without_preference(self):
        port = _find_free_port()
        assert isinstance(port, int)
        assert 1 <= port <= 65535
        assert _is_bindable(port)

    def test_returns_preferred_when_available(self):
        # Descobre uma porta livre e pede ela como preferida.
        free = _find_free_port()
        assert _find_free_port(free) == free

    def test_falls_back_when_preferred_is_busy(self):
        # Ocupa uma porta e confirma que o helper devolve OUTRA porta livre.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            busy_port = occupied.getsockname()[1]

            chosen = _find_free_port(busy_port)
            assert chosen != busy_port
            assert _is_bindable(chosen)

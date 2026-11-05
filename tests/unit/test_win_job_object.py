"""Windows Job Object — testado via mock de `_kernel32()` (o wrapper interno
sobre `ctypes.windll.kernel32`), não do ctypes.windll real — que nem existe
fora do Windows. Roda em qualquer plataforma.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.services import win_job_object


def test_create_job_object_happy_path():
    kernel32 = MagicMock()
    kernel32.CreateJobObjectW.return_value = 42
    kernel32.SetInformationJobObject.return_value = 1

    with patch.object(win_job_object, "_kernel32", return_value=kernel32):
        handle = win_job_object.create_job_object()

    assert handle == 42
    kernel32.SetInformationJobObject.assert_called_once()


def test_create_job_object_createjobobjectw_falha_retorna_none():
    kernel32 = MagicMock()
    kernel32.CreateJobObjectW.return_value = 0  # NULL

    with patch.object(win_job_object, "_kernel32", return_value=kernel32):
        handle = win_job_object.create_job_object()

    assert handle is None
    kernel32.SetInformationJobObject.assert_not_called()


def test_create_job_object_setinformationjobobject_falha_fecha_handle() -> None:
    kernel32 = MagicMock()
    kernel32.CreateJobObjectW.return_value = 42
    kernel32.SetInformationJobObject.return_value = 0  # falha

    with patch.object(win_job_object, "_kernel32", return_value=kernel32):
        handle = win_job_object.create_job_object()

    assert handle is None
    kernel32.CloseHandle.assert_called_once_with(42)


def test_create_job_object_excecao_no_kernel32_nao_lanca():
    with patch.object(win_job_object, "_kernel32", side_effect=OSError("boom")):
        assert win_job_object.create_job_object() is None


def test_assign_process_to_job_happy_path():
    kernel32 = MagicMock()
    kernel32.OpenProcess.return_value = 77
    kernel32.AssignProcessToJobObject.return_value = 1

    with patch.object(win_job_object, "_kernel32", return_value=kernel32):
        ok = win_job_object.assign_process_to_job(42, 1234)

    assert ok is True
    kernel32.CloseHandle.assert_called_once_with(77)


def test_assign_process_to_job_pid_inexistente_retorna_false():
    # Erro/borda: OpenProcess falha (PID não existe/sem permissão) — nunca
    # tenta AssignProcessToJobObject com um handle inválido.
    kernel32 = MagicMock()
    kernel32.OpenProcess.return_value = 0  # NULL

    with patch.object(win_job_object, "_kernel32", return_value=kernel32):
        ok = win_job_object.assign_process_to_job(42, 999999)

    assert ok is False
    kernel32.AssignProcessToJobObject.assert_not_called()


def test_assign_process_to_job_falha_na_associacao_ainda_fecha_handle():
    kernel32 = MagicMock()
    kernel32.OpenProcess.return_value = 77
    kernel32.AssignProcessToJobObject.return_value = 0

    with patch.object(win_job_object, "_kernel32", return_value=kernel32):
        ok = win_job_object.assign_process_to_job(42, 1234)

    assert ok is False
    kernel32.CloseHandle.assert_called_once_with(77)


def test_assign_process_to_job_excecao_nao_lanca():
    with patch.object(win_job_object, "_kernel32", side_effect=OSError("boom")):
        assert win_job_object.assign_process_to_job(42, 1234) is False

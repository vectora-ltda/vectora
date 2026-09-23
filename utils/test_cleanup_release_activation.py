import pytest
from cleanup_release_activation import CleanupOperations, cleanup_activation_fallback


def test_limpeza_ignora_pr_fechada_em_corrida() -> None:
    closed: list[str] = []
    branch_deleted: list[bool] = []

    def close(_number: str) -> None:
        raise RuntimeError("PR já fechada")

    cleanup_activation_fallback(
        CleanupOperations(
            list_open_pull_requests=lambda: ["42"],
            close_pull_request=close,
            pull_request_state=lambda _number: "CLOSED",
            branch_exists=lambda: True,
            delete_branch=lambda: branch_deleted.append(True),
        )
    )

    assert closed == []
    assert branch_deleted == [True]


def test_limpeza_ignora_branch_removida_em_corrida() -> None:
    checks = iter([True, False])

    def delete() -> None:
        raise RuntimeError("branch já removida")

    cleanup_activation_fallback(
        CleanupOperations(
            list_open_pull_requests=lambda: [],
            close_pull_request=lambda _number: None,
            pull_request_state=lambda _number: "CLOSED",
            branch_exists=lambda: next(checks),
            delete_branch=delete,
        )
    )


def test_limpeza_propaga_falha_real_ao_fechar_pr() -> None:
    with pytest.raises(RuntimeError, match="permissão"):
        cleanup_activation_fallback(
            CleanupOperations(
                list_open_pull_requests=lambda: ["42"],
                close_pull_request=lambda _number: (_ for _ in ()).throw(
                    RuntimeError("permissão negada")
                ),
                pull_request_state=lambda _number: "OPEN",
                branch_exists=lambda: False,
                delete_branch=lambda: None,
            )
        )

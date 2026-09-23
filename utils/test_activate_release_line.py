from pathlib import Path

import pytest
from activate_release_line import activation_push_mode, copy_release_files


def test_copia_todos_os_arquivos_de_ativacao(tmp_path: Path) -> None:
    source = {
        "release-lines.json": '{"maintenance": "release/0.2"}\n',
        "config.json": '{"release-as": "0.3.0"}\n',
    }

    def reader(_ref: str, filename: str) -> str:
        return source[filename]

    copied = copy_release_files(
        "chore/rotate-v0.2.0",
        tmp_path,
        source,
        reader=reader,
    )

    assert copied == tuple(source)
    assert (tmp_path / "release-lines.json").read_text() == source["release-lines.json"]
    assert (tmp_path / "config.json").read_text() == source["config.json"]


def test_rejeita_caminho_de_ativacao_fora_do_checkout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inseguro"):
        copy_release_files(
            "ref", tmp_path, ["../release-lines.json"], reader=lambda *_: ""
        )


@pytest.mark.parametrize(
    ("direct", "existing", "expected"),
    [
        (True, None, "direct"),
        (False, None, "fallback-create"),
        (False, "abc123", "fallback-update"),
    ],
)
def test_distingue_push_direto_e_fallback(
    direct: bool, existing: str | None, expected: str
) -> None:
    assert activation_push_mode(direct, existing) == expected

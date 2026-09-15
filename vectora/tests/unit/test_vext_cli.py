from pathlib import Path

from backend.cli.vext import main
from backend.services.vext_artifact import build_vext


def _source(root: Path) -> None:
    (root / "main.py").write_text(
        "def handle(method: str, params: object) -> object:\n    return params\n",
        encoding="utf-8",
    )
    (root / "vectora-extension.json").write_text(
        '{"id":"cli.test","publisher":"local","name":"CLI",'
        '"version":"1.0.0","api_version":1,"protocol_version":1,'
        '"runtime":"python","entrypoint":"main.py","permissions":[]}',
        encoding="utf-8",
    )


def test_rollback_allows_explicitly_unsigned_install(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source)
    artifact = tmp_path / "cli.vext"
    build_vext(source, artifact)
    store = tmp_path / "store"

    assert (
        main(["install", str(artifact), "--root", str(store), "--allow-unsigned"]) == 0
    )
    assert (
        main(
            [
                "rollback",
                "cli.test",
                "1.0.0",
                "--root",
                str(store),
                "--allow-unsigned",
            ]
        )
        == 0
    )

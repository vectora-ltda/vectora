import json
from pathlib import Path

from sort_translation_keys import check_file, sorted_messages


def test_sorted_messages_keeps_metadata_before_alphabetical_keys() -> None:
    assert list(sorted_messages({"z": 1, "$schema": "schema", "a": 2})) == [
        "$schema",
        "a",
        "z",
    ]


def test_check_file_detects_and_repairs_order(tmp_path: Path) -> None:
    path = tmp_path / "pt.json"
    path.write_text(json.dumps({"z": "z", "a": "a"}), encoding="utf-8")
    assert not check_file(path, write=False)
    assert check_file(path, write=True)
    assert list(json.loads(path.read_text(encoding="utf-8"))) == ["a", "z"]

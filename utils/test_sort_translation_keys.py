import json
from pathlib import Path

import sort_translation_keys
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


def test_main_checks_all_paths_after_first_failure(tmp_path: Path, monkeypatch) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps({"z": 1, "a": 2}), encoding="utf-8")
    second.write_text(json.dumps({"z": 3, "a": 4}), encoding="utf-8")
    checked: list[Path] = []

    def check(path: Path, *, write: bool) -> bool:
        checked.append(path)
        return path == second

    monkeypatch.setattr(sort_translation_keys, "check_file", check)
    monkeypatch.setattr("sys.argv", ["sort_translation_keys", str(first), str(second)])

    assert sort_translation_keys.main() == 1
    assert checked == [first, second]

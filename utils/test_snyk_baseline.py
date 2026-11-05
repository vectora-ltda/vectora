import json
from datetime import date
from pathlib import Path

import pytest
import snyk_baseline


def result(rule: str, uri: str, line: int = 1) -> dict:
    return {
        "ruleId": rule,
        "message": {"text": "texto"},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": uri},
                    "region": {"startLine": line},
                }
            }
        ],
    }


def write_sarif(path: Path, results: list[dict]) -> Path:
    path.write_text(json.dumps({"runs": [{"results": results}]}), encoding="utf-8")
    return path


def entry(key: str, expires: str = "2099-01-01") -> dict:
    return {"key": key, "reason": "motivo", "expires": expires}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "a.py").write_text("x = 1\nrun(user_input)\nrun(user_input)\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_key_follows_the_line_text_not_its_position(repo: Path) -> None:
    before = snyk_baseline.finding_key(result("python/PT", "a.py", 2), repo)
    (repo / "a.py").write_text("# novo\nx = 1\nrun(user_input)\n")
    moved = snyk_baseline.finding_key(result("python/PT", "a.py", 3), repo)
    assert before == moved
    (repo / "a.py").write_text("x = 1\nrun(other_input)\n")
    changed = snyk_baseline.finding_key(result("python/PT", "a.py", 2), repo)
    assert changed != before


def test_identical_lines_get_distinct_keys(repo: Path) -> None:
    sarif = write_sarif(
        repo / "s.sarif",
        [result("python/PT", "a.py", 2), result("python/PT", "a.py", 3)],
    )
    findings = snyk_baseline.load_findings(sarif, repo)
    keys = sorted(findings)
    assert len(keys) == 2
    assert keys[1] == f"{keys[0]}#2"


def test_compare_accepts_known_rejects_new_and_flags_expired() -> None:
    findings = {"r|a.py|1": {}, "r|b.py|2": {}}
    new, expired, missing = snyk_baseline.compare(
        findings, [entry("r|a.py|1"), entry("r|c.py|3")], date(2026, 11, 5)
    )
    assert new == ["r|b.py|2"]
    assert expired == []
    assert missing == ["r|c.py|3"]

    new, expired, _ = snyk_baseline.compare(
        {"r|a.py|1": {}}, [entry("r|a.py|1", "2026-01-01")], date(2026, 11, 5)
    )
    assert new == expired == ["r|a.py|1"]


def test_main_exit_codes(repo: Path) -> None:
    sarif = write_sarif(repo / "s.sarif", [result("python/PT", "a.py", 2)])
    key = snyk_baseline.finding_key(result("python/PT", "a.py", 2), repo)
    baseline = repo / "b.json"
    baseline.write_text(json.dumps({"accepted": [entry(key)]}), encoding="utf-8")
    assert snyk_baseline.main(["x", str(sarif), str(baseline)]) == 0

    extra = write_sarif(
        repo / "e.sarif",
        [result("python/PT", "a.py", 2), result("python/CommandInjection", "a.py", 1)],
    )
    assert snyk_baseline.main(["x", str(extra), str(baseline)]) == 1
    assert snyk_baseline.main(["x"]) == 2

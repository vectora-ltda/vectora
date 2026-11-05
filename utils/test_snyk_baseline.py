import json
from datetime import date
from pathlib import Path

import snyk_baseline


def result(rule: str, uri: str, line_hash: str = "abc:1") -> dict:
    return {
        "ruleId": rule,
        "message": {"text": "texto"},
        "locations": [{"physicalLocation": {"artifactLocation": {"uri": uri}}}],
        "partialFingerprints": {"primaryLocationLineHash": line_hash},
    }


def write_sarif(path: Path, results: list[dict]) -> Path:
    path.write_text(json.dumps({"runs": [{"results": results}]}), encoding="utf-8")
    return path


def entry(key: str, expires: str = "2099-01-01") -> dict:
    return {"key": key, "reason": "motivo", "expires": expires}


def test_key_includes_rule_file_and_line_hash() -> None:
    assert (
        snyk_baseline.finding_key(result("python/PT", "a.py", "h:1"))
        == "python/PT|a.py|h:1"
    )


def test_all_findings_in_baseline_pass() -> None:
    findings = {"python/PT|a.py|abc:1": result("python/PT", "a.py")}
    new, expired, missing = snyk_baseline.compare(
        findings, [entry("python/PT|a.py|abc:1")], date(2026, 11, 5)
    )
    assert (new, expired, missing) == ([], [], [])


def test_unknown_finding_is_new_and_changed_line_is_not_accepted() -> None:
    findings = {"python/PT|a.py|zzz:1": result("python/PT", "a.py", "zzz:1")}
    new, _, missing = snyk_baseline.compare(
        findings, [entry("python/PT|a.py|abc:1")], date(2026, 11, 5)
    )
    assert new == ["python/PT|a.py|zzz:1"]
    assert missing == ["python/PT|a.py|abc:1"]


def test_expired_entry_counts_as_new() -> None:
    findings = {"python/PT|a.py|abc:1": result("python/PT", "a.py")}
    new, expired, _ = snyk_baseline.compare(
        findings, [entry("python/PT|a.py|abc:1", "2026-01-01")], date(2026, 11, 5)
    )
    assert new == expired == ["python/PT|a.py|abc:1"]


def test_main_exit_codes(tmp_path: Path) -> None:
    sarif = write_sarif(tmp_path / "s.sarif", [result("python/PT", "a.py")])
    baseline = tmp_path / "b.json"
    baseline.write_text(
        json.dumps({"accepted": [entry("python/PT|a.py|abc:1")]}), encoding="utf-8"
    )
    assert snyk_baseline.main(["x", str(sarif), str(baseline)]) == 0

    extra = write_sarif(
        tmp_path / "e.sarif",
        [result("python/PT", "a.py"), result("python/CommandInjection", "b.py")],
    )
    assert snyk_baseline.main(["x", str(extra), str(baseline)]) == 1
    assert snyk_baseline.main(["x"]) == 2

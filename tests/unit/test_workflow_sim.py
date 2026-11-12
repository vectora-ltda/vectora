"""Testes do simulador de workflows (utils/workflow_sim.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "utils"))

import workflow_sim as sim
from workflow_sim import Rule, Sandbox, UnsupportedExpression


def _workflow(tmp_path: Path, steps: str, job_env: str = "") -> Path:
    path = tmp_path / "wf.yml"
    path.write_text(
        "name: t\non: push\njobs:\n  job:\n    runs-on: ubuntu-24.04\n"
        + job_env
        + "    steps:\n"
        + steps,
        encoding="utf-8",
    )
    return path


# ── expressões ─────────────────────────────────────────────────────────────


def test_expression_lookup_and_fallback() -> None:
    context = {"github": {"token": "default"}, "secrets": {"X": ""}}

    assert sim.render("${{ github.token }}", context) == "default"
    assert sim.render("${{ secrets.X || github.token }}", context) == "default"
    context["secrets"]["X"] = "privado"
    assert sim.render("${{ secrets.X || github.token }}", context) == "privado"


def test_expression_missing_key_renders_empty_like_actions() -> None:
    assert sim.render("a${{ secrets.NAO_EXISTE }}b", {"secrets": {}}) == "ab"


def test_unsupported_expression_fails_closed() -> None:
    with pytest.raises(UnsupportedExpression):
        sim.render("${{ format('{0}', github.sha) }}", {"github": {"sha": "x"}})


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("always()", True),
        ("success()", True),
        ("github.event_name == 'push'", True),
        ("github.event_name == 'push' && github.ref == 'refs/heads/dev'", False),
        ("github.ref == 'refs/heads/dev' || github.event_name == 'push'", True),
        ("!(github.event_name == 'push')", False),
    ],
)
def test_condition_evaluation(expression: str, expected: bool) -> None:
    context = {"github": {"event_name": "push", "ref": "refs/heads/master"}}

    assert sim.evaluate_condition(expression, context) is expected


# ── gh falso ───────────────────────────────────────────────────────────────


def test_fake_gh_fails_closed_when_a_call_has_no_rule(tmp_path: Path) -> None:
    box = Sandbox(tmp_path / "box", tools=("gh",))
    wf = _workflow(tmp_path, "      - run: gh release delete v1\n")

    results = sim.run_job(wf, "job", sandbox=box, context={}, workdir=tmp_path)

    assert results[0].exit_code == 97
    assert "sem regra" in results[0].output


def test_fake_gh_answers_from_rules_and_records_calls_and_token(
    tmp_path: Path,
) -> None:
    box = Sandbox(tmp_path / "box", tools=("gh",))
    box.set_rules("gh", [Rule(["api", "*", "--jq", ".sha"], stdout="abc123\n")])
    wf = _workflow(
        tmp_path,
        "      - env:\n          GH_TOKEN: ${{ secrets.T || github.token }}\n"
        "        run: |\n          sha=$(gh api repos/x/commits/v1 --jq .sha)\n"
        '          test "$sha" = abc123\n',
    )
    context = {"github": {"token": "padrao"}, "secrets": {"T": ""}}

    results = sim.run_job(wf, "job", sandbox=box, context=context, workdir=tmp_path)

    assert results[0].exit_code == 0
    [call] = box.calls("gh")
    assert call.args == ["api", "repos/x/commits/v1", "--jq", ".sha"]
    assert call.token == "padrao"


def test_host_gh_credentials_are_not_inherited_by_the_simulated_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_TOKEN", "host-secret-marker")
    monkeypatch.setenv("GITHUB_TOKEN", "host-github-secret-marker")
    box = Sandbox(tmp_path / "box", tools=("gh",))
    box.set_rules("gh", [Rule(["api", "repos/x/r"], stdout="{}")])
    wf = _workflow(tmp_path, "      - run: gh api repos/x/r\n")

    results = sim.run_job(wf, "job", sandbox=box, context={}, workdir=tmp_path)

    assert results[0].exit_code == 0
    [call] = box.calls("gh")
    assert call.token == ""


def test_scenario_with_only_pnpm_rules_still_routes_gh_to_the_fake_shim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wf = _workflow(tmp_path, "      - run: gh api repos/o/r\n")
    scenario = tmp_path / "cenario.json"
    scenario.write_text(
        json.dumps({"context": {}, "rules": {"pnpm": [{"args": ["install"]}]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GH_TOKEN", "host-secret-marker")

    code = sim.main([str(wf), "job", str(scenario)])

    output = capsys.readouterr().out
    assert code == 1
    assert "gh-sim: chamada sem regra" in output
    assert "host-secret-marker" not in output


def test_release_upload_expands_glob_characters_like_the_real_gh(
    tmp_path: Path,
) -> None:
    """Regressão do erro real: `no matches found for ...[Content_Types].xml`."""
    work = tmp_path / "work"
    (work / "pasta").mkdir(parents=True)
    (work / "pasta" / "[Content_Types].xml").write_text("<x/>")
    (work / "pasta" / "normal.exe").write_text("x")
    box = Sandbox(tmp_path / "box", tools=("gh",))
    box.set_rules("gh", [Rule(["release", "upload"])])
    wf = _workflow(
        tmp_path,
        "      - run: gh release upload v1 pasta/normal.exe --repo o/r --clobber\n"
        "      - run: gh release upload v1 'pasta/[Content_Types].xml' --repo o/r\n",
    )

    results = sim.run_job(wf, "job", sandbox=box, context={}, workdir=work)

    assert [r.exit_code for r in results] == [0, 1]
    assert "no matches found" in results[1].output
    assert box.uploaded_files() == ["pasta/normal.exe"]


# ── execução do job ────────────────────────────────────────────────────────


def test_steps_share_outputs_and_env_and_stop_at_the_first_failure(
    tmp_path: Path,
) -> None:
    box = Sandbox(tmp_path / "box", tools=("gh",))
    wf = _workflow(
        tmp_path,
        '      - id: ver\n        run: echo "tag=v$((1+2))" >> "$GITHUB_OUTPUT"\n'
        "      - name: usa\n        env:\n          TAG: ${{ steps.ver.outputs.tag }}\n"
        '        run: test "$TAG" = v3\n'
        "      - name: falha\n        run: exit 7\n"
        "      - name: nunca roda\n        run: echo nao\n",
    )

    results = sim.run_job(wf, "job", sandbox=box, context={}, workdir=tmp_path)

    assert [(r.name, r.exit_code) for r in results] == [
        (None, 0),
        ("usa", 0),
        ("falha", 7),
    ]


def test_uses_steps_are_reported_as_skipped(tmp_path: Path) -> None:
    box = Sandbox(tmp_path / "box", tools=("gh",))
    wf = _workflow(
        tmp_path,
        "      - uses: actions/checkout@v7\n      - run: echo ok\n",
    )

    results = sim.run_job(wf, "job", sandbox=box, context={}, workdir=tmp_path)

    assert results[0].skipped is not None
    assert "actions/checkout" in results[0].skipped
    assert results[1].exit_code == 0


def test_step_condition_false_skips_the_step(tmp_path: Path) -> None:
    box = Sandbox(tmp_path / "box", tools=("gh",))
    wf = _workflow(
        tmp_path,
        "      - if: github.ref == 'refs/heads/dev'\n        run: exit 1\n"
        "      - run: echo ok\n",
    )
    context = {"github": {"ref": "refs/heads/master"}}

    results = sim.run_job(wf, "job", sandbox=box, context=context, workdir=tmp_path)

    assert results[0].skipped == "condição falsa"
    assert results[1].exit_code == 0


def test_simultaneous_calls_are_all_recorded_without_corrupting_the_log(
    tmp_path: Path,
) -> None:
    """`diff <(gh ...) <(gh ...)` roda dois processos ao mesmo tempo; o registro
    das chamadas não pode misturar as duas escritas."""
    box = Sandbox(tmp_path / "box", tools=("gh",))
    box.set_rules("gh", [Rule(["api", "*"], stdout="ok\n")])
    wf = _workflow(
        tmp_path,
        "      - run: |\n"
        "          for i in 1 2 3 4 5 6 7 8; do\n"
        "            diff <(gh api a$i) <(gh api b$i) >/dev/null\n"
        "          done\n",
    )

    results = sim.run_job(wf, "job", sandbox=box, context={}, workdir=tmp_path)

    assert results[0].exit_code == 0
    assert len(box.calls("gh")) == 16


def test_cli_runs_a_scenario_file_and_reports_the_calls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wf = _workflow(tmp_path, "      - run: gh api repos/o/r\n")
    scenario = tmp_path / "cenario.json"
    scenario.write_text(
        json.dumps(
            {
                "context": {},
                "rules": {"gh": [{"args": ["api", "*"], "stdout": "{}"}]},
                "files": {"a.txt": "x"},
            }
        ),
        encoding="utf-8",
    )

    code = sim.main([str(wf), "job", str(scenario)])

    out = capsys.readouterr().out
    assert code == 0
    assert "gh api repos/o/r" in out

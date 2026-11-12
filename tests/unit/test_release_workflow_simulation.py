"""Simula os jobs de release do vectora.yml com um `gh` falso.

Roda os passos `run:` reais de `tag-release` e `publish-update-channel` (ver
utils/workflow_sim.py). O `gh` só responde ao que cada cenário prevê; qualquer
outra chamada falha. Toda mudança nesses jobs entra aqui antes do PR.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "utils"))

import workflow_sim as sim
from workflow_sim import Call, Rule, Sandbox, StepResult

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "vectora.yml"
REPO = "vectora-company/vectora"
SHA = "d0873cb2eb81bee64a0c98849649af9b8154baab"
OTHER_SHA = "726426e3ff9af4627158a52893fc29d8e426459f"
TAG_OBJECT = "a" * 40
DEFAULT_TOKEN = "token-padrao-do-actions"
RELEASE_TOKEN = "token-com-permissao-de-workflows"

INSTALLERS = [
    "vectora-win-x64/Vectora-0.1.24-win-x64.exe",
    "vectora-win-x64/Vectora-0.1.24-win-x64.exe.blockmap",
    "vectora-win-x64/Vectora-0.1.24-win-x64.msi",
    "vectora-mac-arm64/Vectora-0.1.24-mac-arm64.dmg",
    "vectora-linux-x64/Vectora-0.1.24-linux-x64.AppImage",
    "vectora-linux-x64/Vectora-0.1.24-linux-x64.deb",
]
NOISE = [
    "vectora-win-x64/latest.yml",
    "vectora-linux-x64/latest-linux.yml",
    "vectora-linux-x64/builder-debug.yml",
    "vectora-win-x64/win-unpacked/Vectora.exe",
]
CHANGELOG = (
    "# Changelog\n\n## [0.1.24] (2026-11-04)\n\n### Features\n\n- feat: algo (#1)\n"
)


def _prepare(
    tmp_path: Path,
    *,
    files: list[str] | None = None,
    changelog: str = CHANGELOG,
) -> Path:
    """Monta uma cópia temporária mínima dos artefatos usados pela release."""
    work = tmp_path / "work"
    (work / "utils").mkdir(parents=True)
    for name in (
        "release_assets.py",
        "release_notes.py",
        "sign_release_artifacts.py",
    ):
        shutil.copy(REPO_ROOT / "utils" / name, work / "utils" / name)
    (work / "pyproject.toml").write_text(
        '[project]\nname = "vectora"\nversion = "0.1.24"\n', encoding="utf-8"
    )
    (work / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    for relative in INSTALLERS + NOISE if files is None else files:
        target = work / "dist-release" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"conteudo de {relative}", encoding="utf-8")
    return work


def _context(*, release_token: str = "") -> dict[str, object]:
    return {
        "github": {
            "sha": SHA,
            "repository": REPO,
            "token": DEFAULT_TOKEN,
            "ref": "refs/heads/master",
            "event_name": "push",
            "event": {"repository": {"default_branch": "master"}},
        },
        "secrets": {
            "RELEASE_TAG_TOKEN": release_token,
            "GPG_PRIVATE_KEY": "",
            "GPG_PASSPHRASE": "",
            "CLOUDFLARE_API_TOKEN": "cf",
            "CLOUDFLARE_ACCOUNT_ID": "conta",
            "R2_ACCESS_KEY_ID": "r2id",
            "R2_SECRET_ACCESS_KEY": "r2secret",
        },
        "needs": {
            "release-channel": {"outputs": {"channel": "latest", "version": "0.1.24"}},
            "release-trigger": {"outputs": {"is_release": "true"}},
        },
    }


def _gh_rules(
    *,
    tag_at: str | None = None,
    release_exists: bool = False,
    release_draft: bool = True,
    diverged: bool = False,
    upload_exit: int = 0,
    tag_lookup_error: str | None = None,
) -> list[Rule]:
    """Declara as respostas de GitHub esperadas para um cenário de release."""
    api = f"repos/{REPO}"
    tag_lookup = (
        Rule(
            ["api", f"{api}/git/ref/tags/v0.1.24", "--jq", ".object.sha"],
            stdout=f"{tag_at}\n",
        )
        if tag_at
        else Rule(
            ["api", f"{api}/git/ref/tags/v0.1.24", "--jq", ".object.sha"],
            stderr=tag_lookup_error,
            exit=1,
        )
        if tag_lookup_error
        else Rule(
            ["api", f"{api}/git/ref/tags/v0.1.24", "--jq", ".object.sha"],
            stderr="gh: Not Found (HTTP 404)\n",
            exit=1,
        )
    )
    return [
        Rule(
            ["api", f"{api}/contents/.github/workflows?ref={SHA}", "--jq", "*"],
            stdout=".github/workflows/pr-checks.yml aaa\n",
        ),
        Rule(
            ["api", f"{api}/contents/.github/workflows?ref=master", "--jq", "*"],
            stdout=(
                ".github/workflows/pr-checks.yml bbb\n"
                if diverged
                else ".github/workflows/pr-checks.yml aaa\n"
            ),
        ),
        tag_lookup,
        Rule(["api", f"{api}/commits/v0.1.24", "--jq", ".sha"], stdout=f"{tag_at}\n"),
        Rule(["api", f"{api}/git/tags"], stdout=f"{TAG_OBJECT}\n"),
        Rule(["api", f"{api}/git/refs"], stdout="{}\n"),
        Rule(
            [
                "release",
                "view",
                "v0.1.24",
                "--repo",
                REPO,
                "--json",
                "isDraft",
                "--jq",
                ".isDraft",
            ],
            stdout=f"{str(release_draft).lower()}\n",
        ),
        Rule(["release", "view", "v0.1.24"], exit=0 if release_exists else 1),
        Rule(["release", "create", "v0.1.24"]),
        Rule(["release", "upload"], exit=upload_exit),
        Rule(["release", "edit", "v0.1.24"]),
        Rule(["workflow", "run", "rotate-release-lines.yml"]),
    ]


def _run_tag_release(
    tmp_path: Path,
    *,
    rules: list[Rule] | None = None,
    release_token: str = "",
    files: list[str] | None = None,
    changelog: str = CHANGELOG,
) -> tuple[list[StepResult], Sandbox]:
    """Executa a publicação com arquivos e respostas falsas controladas."""
    box = Sandbox(tmp_path / "sandbox", tools=("gh",))
    box.set_rules("gh", _gh_rules() if rules is None else rules)
    work = _prepare(tmp_path, files=files, changelog=changelog)
    results = sim.run_job(
        WORKFLOW,
        "tag-release",
        sandbox=box,
        context=_context(release_token=release_token),
        workdir=work,
    )
    return results, box


def _executed(results: list[StepResult]) -> list[StepResult]:
    """Remove passos ignorados e mantém apenas os que o job executou."""
    return [result for result in results if result.skipped is None]


def _verbs(calls: list[Call]) -> list[str]:
    """Resume cada chamada: `api .../git/tags`, `release create`, `release upload`..."""
    return [" ".join(call.args[:2]) for call in calls]


def _step(results: list[StepResult], name: str) -> StepResult:
    """Localiza o resultado do passo pelo nome exibido no workflow."""
    return next(result for result in results if result.name == name)


# ── tag-release ────────────────────────────────────────────────────────────


def test_first_release_creates_the_tag_and_the_release_and_attaches_only_installers(
    tmp_path: Path,
) -> None:
    """Verifica a criação inicial e a seleção segura dos anexos da release."""
    results, box = _run_tag_release(tmp_path)

    assert [r.exit_code for r in _executed(results)] == [0] * len(_executed(results))
    uploaded = [Path(name).name for name in box.uploaded_files()]
    assert sorted(uploaded) == sorted(
        [
            "Vectora-0.1.24-win-x64.exe",
            "Vectora-0.1.24-win-x64.exe.blockmap",
            "Vectora-0.1.24-win-x64.msi",
            "Vectora-0.1.24-mac-arm64.dmg",
            "Vectora-0.1.24-linux-x64.AppImage",
            "Vectora-0.1.24-linux-x64.deb",
            "SHA256SUMS",
        ]
    )
    assert not any("[" in name for name in box.uploaded_files())
    verbs = _verbs(box.calls("gh"))
    assert verbs.index(f"api repos/{REPO}/git/tags") < verbs.index(
        f"api repos/{REPO}/git/refs"
    )
    assert verbs.index(f"api repos/{REPO}/git/refs") < verbs.index("release create")
    assert verbs.index("release create") < verbs.index("release upload")
    assert verbs[-1] == "workflow run"


def test_the_tag_is_created_through_the_api_as_an_annotated_tag(tmp_path: Path) -> None:
    """Verifica os campos enviados à API para criar uma tag anotada."""
    _, box = _run_tag_release(tmp_path)

    tag_call = next(c for c in box.calls("gh") if c.args[1].endswith("/git/tags"))
    ref_call = next(c for c in box.calls("gh") if c.args[1].endswith("/git/refs"))
    assert f"object={SHA}" in tag_call.args
    assert "tag=v0.1.24" in tag_call.args
    assert "type=commit" in tag_call.args
    assert "ref=refs/tags/v0.1.24" in ref_call.args
    assert f"sha={TAG_OBJECT}" in ref_call.args


def test_release_body_comes_from_the_changelog_section(tmp_path: Path) -> None:
    """Verifica que a release usa as notas existentes no changelog."""
    _, box = _run_tag_release(tmp_path)

    create = next(c for c in box.calls("gh") if c.args[:2] == ["release", "create"])
    assert "--notes-file" in create.args
    assert "--generate-notes" not in create.args
    assert "--verify-tag" in create.args
    assert "Vectora 0.1.24" in create.args


def test_release_falls_back_to_generated_notes_without_a_changelog_section(
    tmp_path: Path,
) -> None:
    """Verifica o fallback às notas do GitHub quando a versão não está no changelog."""
    _, box = _run_tag_release(tmp_path, changelog="# Changelog\n\n## [0.1.23]\n\n- x\n")

    create = next(c for c in box.calls("gh") if c.args[:2] == ["release", "create"])
    assert "--generate-notes" in create.args
    assert "--notes-file" not in create.args


def test_an_existing_tag_on_the_same_commit_is_reused(tmp_path: Path) -> None:
    results, box = _run_tag_release(tmp_path, rules=_gh_rules(tag_at=SHA))

    assert all(r.exit_code == 0 for r in _executed(results))
    assert not any(c.args[1].endswith("/git/tags") for c in box.calls("gh"))
    assert not any(c.args[1].endswith("/git/refs") for c in box.calls("gh"))
    assert "release create" in _verbs(box.calls("gh"))


def test_an_existing_tag_on_another_commit_stops_before_any_release(
    tmp_path: Path,
) -> None:
    results, box = _run_tag_release(tmp_path, rules=_gh_rules(tag_at=OTHER_SHA))

    failed = _step(results, "Create tag")
    assert failed.exit_code == 1
    assert OTHER_SHA in failed.output
    assert "release create" not in _verbs(box.calls("gh"))
    assert box.uploaded_files() == []


def test_an_existing_release_is_not_created_again_but_gets_its_assets(
    tmp_path: Path,
) -> None:
    results, box = _run_tag_release(
        tmp_path, rules=_gh_rules(release_exists=True, release_draft=False)
    )

    assert all(r.exit_code == 0 for r in _executed(results))
    assert "release create" not in _verbs(box.calls("gh"))
    assert box.uploaded_files()
    assert "release edit" not in _verbs(box.calls("gh"))


def test_upload_failure_keeps_release_as_draft_and_a_retry_publishes_it(
    tmp_path: Path,
) -> None:
    """Garante que falha de anexo mantém o rascunho e uma nova tentativa o publica."""
    failed_results, failed_box = _run_tag_release(
        tmp_path / "failed", rules=_gh_rules(upload_exit=1)
    )

    assert _step(failed_results, "Attach release assets").exit_code == 1
    assert "--draft" in next(
        call.args
        for call in failed_box.calls("gh")
        if call.args[:2] == ["release", "create"]
    )
    assert "release edit" not in _verbs(failed_box.calls("gh"))
    assert not any(result.name == "Publish GitHub Release" for result in failed_results)

    retry_results, retry_box = _run_tag_release(
        tmp_path / "retry",
        rules=_gh_rules(release_exists=True, release_draft=True),
    )

    assert all(result.exit_code == 0 for result in _executed(retry_results))
    publish_call = next(
        call for call in retry_box.calls("gh") if call.args[:2] == ["release", "edit"]
    )
    assert "--draft=false" in publish_call.args


def test_without_installers_the_job_fails_before_touching_github(
    tmp_path: Path,
) -> None:
    results, box = _run_tag_release(tmp_path, files=NOISE)

    assert _step(results, "Select release assets").exit_code == 1
    assert box.calls("gh") == []


def test_every_attached_name_is_safe_for_the_upload_glob(tmp_path: Path) -> None:
    """Regressão do erro real: `no matches found for ...[Content_Types].xml`."""
    results, box = _run_tag_release(tmp_path)

    assert _step(results, "Attach release assets").exit_code == 0
    assert all(
        all(char.isalnum() or char in "._+-/" for char in name)
        for name in box.uploaded_files()
    )


def test_a_default_token_is_used_for_tag_and_release_when_no_release_token_exists(
    tmp_path: Path,
) -> None:
    _, box = _run_tag_release(tmp_path)

    assert {call.token for call in box.calls("gh")} == {DEFAULT_TOKEN}


def test_diverged_workflows_warn_and_the_release_token_takes_over(
    tmp_path: Path,
) -> None:
    results, box = _run_tag_release(
        tmp_path, rules=_gh_rules(diverged=True), release_token=RELEASE_TOKEN
    )

    assert all(r.exit_code == 0 for r in _executed(results))
    by_verb = {" ".join(c.args[:2]): c.token for c in box.calls("gh")}
    assert by_verb["release create"] == RELEASE_TOKEN
    assert by_verb["release upload"] == RELEASE_TOKEN
    assert by_verb[f"api repos/{REPO}/git/refs"] == RELEASE_TOKEN
    # A rotação das linhas usa o token do Actions, que é quem pode disparar workflows.
    assert by_verb["workflow run"] == DEFAULT_TOKEN


def test_diverged_workflows_without_a_release_token_warn_but_still_try(
    tmp_path: Path,
) -> None:
    results, box = _run_tag_release(tmp_path, rules=_gh_rules(diverged=True))

    guard = _step(results, "Check the release commit workflows")
    assert guard.exit_code == 0
    assert "::warning::" in guard.output
    assert "RELEASE_TAG_TOKEN" in guard.output
    assert {call.token for call in box.calls("gh")} == {DEFAULT_TOKEN}


def test_matching_workflows_do_not_warn(tmp_path: Path) -> None:
    results, _ = _run_tag_release(tmp_path)

    assert (
        "::warning::" not in _step(results, "Check the release commit workflows").output
    )


def test_a_gh_call_without_a_rule_fails_the_simulation(tmp_path: Path) -> None:
    """Confirma que chamada GitHub sem regra interrompe o job de release."""
    rules = [rule for rule in _gh_rules() if rule.args[:2] != ["release", "create"]]

    results, _ = _run_tag_release(tmp_path, rules=rules)

    created = _step(results, "Create GitHub Release")
    assert created.exit_code == 97
    assert "sem regra" in created.output


def test_tag_lookup_server_error_aborts_without_creating_a_tag(
    tmp_path: Path,
) -> None:
    results, box = _run_tag_release(
        tmp_path,
        rules=_gh_rules(tag_lookup_error="gh: Service Unavailable (HTTP 503)\n"),
    )

    failed = _step(results, "Create tag")
    assert failed.exit_code != 0
    assert "HTTP 503" in failed.output
    assert not any(call.args[1].endswith("/git/tags") for call in box.calls("gh"))
    assert not any(call.args[1].endswith("/git/refs") for call in box.calls("gh"))


# ── publish-update-channel ─────────────────────────────────────────────────


def _run_publish(tmp_path: Path) -> tuple[list[StepResult], Sandbox]:
    box = Sandbox(tmp_path / "sandbox", tools=("gh", "pnpm"))
    box.set_rules("pnpm", [Rule(["install"]), Rule(["run", "release"])])
    work = tmp_path / "work"
    (work / "release-worker").mkdir(parents=True)
    results = sim.run_job(
        WORKFLOW,
        "publish-update-channel",
        sandbox=box,
        context=_context(),
        workdir=work,
    )
    return results, box


def test_the_update_channel_publishes_only_a_manifest_that_points_to_the_release(
    tmp_path: Path,
) -> None:
    """Verifica que o canal publica manifesto apontando para os anexos da release."""
    results, box = _run_publish(tmp_path)

    assert all(r.exit_code == 0 for r in _executed(results))
    [publish] = [c for c in box.calls("pnpm") if c.args[:2] == ["run", "release"]]
    assert "--channel=latest" in publish.args
    assert "--version=0.1.24" in publish.args
    assert f"--release-url=https://github.com/{REPO}/releases/download/v0.1.24" in (
        publish.args
    )
    assert any(arg.startswith("--dist=") for arg in publish.args)
    assert box.calls("gh") == []


def test_the_manifest_is_published_only_after_the_release_exists() -> None:
    import yaml

    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]

    assert "tag-release" in jobs["publish-update-channel"]["needs"]
    assert "publish-update-channel" not in jobs["tag-release"]["needs"]


@pytest.mark.parametrize("job", ["tag-release", "publish-update-channel"])
def test_release_jobs_run_only_for_a_promotion_on_master(job: str) -> None:
    import yaml

    condition = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"][job]["if"]

    assert "github.ref == 'refs/heads/master'" in condition
    assert "needs.release-trigger.outputs.is_release == 'true'" in condition
    assert "workflow_dispatch" not in condition


def test_master_release_pushes_are_not_cancelled_or_coalesced() -> None:
    import yaml

    concurrency = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["concurrency"]
    first_context = {
        "github": {
            "event_name": "push",
            "ref": "refs/heads/master",
            "run_id": "101",
        }
    }
    second_context = {
        "github": {
            "event_name": "push",
            "ref": "refs/heads/master",
            "run_id": "102",
        }
    }

    first_group = sim.render(concurrency["group"], first_context)
    second_group = sim.render(concurrency["group"], second_context)
    cancel = concurrency["cancel-in-progress"]

    assert first_group != second_group
    assert (
        sim.evaluate_condition(
            cancel.removeprefix("${{").removesuffix("}}"), first_context
        )
        is False
    )
    assert (
        sim.evaluate_condition(
            cancel.removeprefix("${{").removesuffix("}}"), second_context
        )
        is False
    )

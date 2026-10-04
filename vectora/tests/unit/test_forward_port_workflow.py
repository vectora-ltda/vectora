"""Testes dos contratos dos workflows de promoção e release."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

WORKFLOW: Path = (
    Path(__file__).resolve().parents[2]
    / ".."
    / ".github"
    / "workflows"
    / "forward-port-release.yml"
).resolve()
CONFIG: Path = WORKFLOW.parent.parent / "release-lines.json"


def test_forward_port_workflow_promotes_stable_master_into_active_minor() -> None:
    """Protege a promoção unidirecional configurada, as tentativas e a branch confiável."""
    content = WORKFLOW.read_text(encoding="utf-8")

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert ".github/release-lines.json" in content
    assert '"release/**"' in content
    assert 'echo "enabled=false" >> "$GITHUB_OUTPUT"' in content
    assert (
        "github.ref_name == steps.release-lines.outputs.maintenance_branch" in content
    )
    assert 'git merge-base --is-ancestor "origin/$STABLE_BRANCH" HEAD' in content
    assert 'git merge --no-edit "origin/$STABLE_BRANCH"' in content
    assert "\\$STABLE_BRANCH" not in content
    assert 'echo "maintenance_branch=$maintenance_branch"' in content
    assert 'git push origin "HEAD:$DEVELOPMENT_BRANCH"' in content
    assert "for attempt in 1 2 3" in content
    assert "pull-requests: write" in content
    assert 'base_sha="$(git rev-parse "origin/$DEVELOPMENT_BRANCH")"' in content
    assert 'remote_sha="$(git rev-parse "origin/$DEVELOPMENT_BRANCH")"' in content
    assert config["development"]["branch"]
    assert config["development"]["milestone"]
    assert config["maintenance"]["branch"]
    assert config["maintenance"]["milestone"]
    assert config["development"]["branch"] != config["maintenance"]["branch"]


def test_forward_port_workflow_opens_isolated_conflict_pr() -> None:
    """Protege a resolução de conflitos contra gravação de commits na manutenção."""
    content = WORKFLOW.read_text(encoding="utf-8")

    assert "git merge --abort || true" in content
    assert (
        'conflict_branch="sync/release-promotion-${STABLE_BRANCH//\\//-}-to-${DEVELOPMENT_BRANCH//\\//-}"'
        in content
    )
    assert "force-with-lease" in content
    assert "preserving resolver commits" in content
    assert 'git push origin "HEAD:$conflict_branch"' in content
    assert '--head "$conflict_branch"' in content
    assert '--milestone "$DEVELOPMENT_MILESTONE"' not in content
    assert "RELEASE_PLEASE_TOKEN: ${{ secrets.RELEASE_PLEASE_TOKEN }}" in content


def test_pr_milestone_workflow_assigns_milestone_from_base() -> None:
    """Garante que bases de PR suportadas sejam associadas às milestones e que corridas sejam recuperadas."""
    workflow = WORKFLOW.parent / "pr-release-milestone.yml"
    content = workflow.read_text(encoding="utf-8")

    assert ".github/release-lines.json" in content
    assert "config.development, config.maintenance" in content
    assert "issues.update" in content
    assert "issues.createMilestone" in content
    assert "error.status !== 422" in content
    assert "CURRENT_RELEASE_MILESTONE" in content
    assert "CURRENT_PR_BASE" in content
    assert "current_milestone" in content
    assert "current_base" in content
    assert "milestone: milestone.number" in content
    assert "issues: write" in content
    assert (
        "github-token: ${{ secrets.RELEASE_PLEASE_TOKEN || github.token }}" in content
    )
    assert "github.event.action == 'opened'" not in content
    assert "context.payload.pull_request.milestone?.title" in content
    assert 'core.setOutput("current_milestone", payloadMilestone)' in content


def test_release_please_uses_trusted_config_for_branch_gate() -> None:
    """Protege o token do Release Please contra configuração controlada pela branch."""
    workflow = WORKFLOW.parent / "release-please.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "github.event.repository.default_branch" in content
    assert "startsWith(github.ref, 'refs/tags/v')" in content
    assert "target-branch:" in content
    assert ".github/release-lines.json" in content
    assert '"release/**"' in content
    assert 'echo "enabled=false" >> "$GITHUB_OUTPUT"' in content
    assert "enabled=false" in content
    assert "RELEASE_PLEASE_TOKEN" in content
    assert "config_file" in content
    assert "manifest_file" in content
    assert "map(select(\\" not in content


def test_release_please_scopes_pr_body_to_current_release_notes() -> None:
    """Garante que o PR não publique o histórico inteiro do changelog."""
    workflow = WORKFLOW.parent / "release-please.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "utils/prepare_release_pr_body.py" in content
    assert "vectora/CHANGELOG.md" in content
    assert 'gh pr edit "$PR_NUMBER" --repo "$GITHUB_REPOSITORY"' in content
    assert '--body-file "$RUNNER_TEMP/release-pr-body.md"' in content
    assert 'echo "number=$number" >> "$GITHUB_OUTPUT"' in content


def test_release_please_requires_candidates_and_uses_versioned_branches() -> None:
    """Evita releases vazias e impede o retorno do nome genérico da automação."""
    workflow = WORKFLOW.parent / "release-please.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "Check for merged release candidates" in content
    assert "git fetch --tags --force origin" in content
    assert "--json milestone,labels,mergedAt" in content
    assert "fromdateiso8601" in content
    assert "latest_tag_epoch" in content
    assert "steps.release-candidates.outputs.has_candidates == 'true'" in content
    assert "Rename generated Release Please branch by version" in content
    assert 'target="release-please-${major}.${minor}"' in content
    assert 'target="release-please-${version}"' in content
    assert "utils/select_release_pr.py" in content
    assert '--phase generated <<< "$prs"' in content
    assert '--phase versioned <<< "$prs"' in content
    assert (
        'git remote set-url origin "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"'
        in content
    )
    find_section = content.split("# Somente o branch exato", 1)[1]
    assert "release-please--branches--" not in find_section


@pytest.mark.parametrize("development", ["release/0.3", "release/0.4"])
async def test_sync_outputs_expand_stable_branch(
    tmp_path: Path, development: str
) -> None:
    """Executa o jq real do workflow contra uma configuração temporária."""
    bash = (
        Path("C:/Program Files/Git/bin/bash.exe")
        if os.name == "nt"
        else Path(shutil.which("bash") or "/bin/bash")
    )
    if not bash.is_file():
        pytest.skip("Bash necessário para executar a etapa do workflow")
    jq = shutil.which("jq")
    if jq is None:
        pytest.skip("jq necessário para executar a etapa do workflow")

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    config["development"]["branch"] = development
    config["development"]["milestone"] = development.removeprefix("release/")
    config_path = tmp_path / ".github" / "release-lines.json"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(config), encoding="utf-8")

    source = WORKFLOW.read_text(encoding="utf-8")
    validation = source.split("          jq -e '\n", 1)[1].split(
        "\n          ' .github/release-lines.json >/dev/null", 1
    )[0]
    variables = {
        "development_branch": "development_branch",
        "maintenance_branch": "maintenance_branch",
        "development_milestone": "development_milestone",
    }
    selectors: dict[str, str] = {}
    for variable, output in variables.items():
        line = next(
            line
            for line in source.splitlines()
            if line.strip().startswith(f'{variable}="$(jq -er ')
        )
        selectors[output] = line.split("jq -er '", 1)[1].split("'", 1)[0]
    output_lines = [
        'echo "enabled=true"',
        *(
            f'echo "{name}=$(jq -er \'{selector}\' "$CONFIG_FILE")"'
            for name, selector in selectors.items()
        ),
    ]
    script = (
        "set -euo pipefail\n"
        f"jq -e '{validation}' \"$CONFIG_FILE\" >/dev/null\n"
        "{\n" + "\n".join(output_lines) + '\n} >> "$GITHUB_OUTPUT"'
    )
    process = await asyncio.create_subprocess_exec(
        str(bash),
        "-euc",
        script,
        cwd=tmp_path,
        env={
            **os.environ,
            "GITHUB_OUTPUT": "output.txt",
            "CONFIG_FILE": str(config_path),
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()
    outputs = dict(
        line.split("=", 1)
        for line in (tmp_path / "output.txt").read_text(encoding="utf-8").splitlines()
    )
    assert outputs["enabled"] == "true"
    assert outputs["maintenance_branch"] == config["maintenance"]["branch"]
    assert outputs["development_branch"] == config["development"]["branch"]
    assert outputs["development_milestone"] == config["development"]["milestone"]


def test_rotation_reconciles_prs_already_moved_to_new_minor() -> None:
    """Recupera PRs com base migrada e milestone antiga após falha parcial."""
    content = (WORKFLOW.parent / "rotate-release-lines.yml").read_text(encoding="utf-8")
    stranded_query = content.split("const stranded =", 1)[1].split(
        "for (const pr of stranded)", 1
    )[0]
    stranded_loop = content.split("for (const pr of stranded)", 1)[1]
    assert "base: DEVELOPMENT_BRANCH" in stranded_query
    assert "pr.milestone?.title !== PREVIOUS_DEVELOPMENT_MILESTONE" in stranded_loop
    assert "issues.update" in stranded_loop
    assert "pulls.update" not in stranded_loop


def test_rotation_continues_after_individual_pr_migration_failure() -> None:
    """Uma falha isolada não deve impedir a migração das PRs seguintes."""
    content = (WORKFLOW.parent / "rotate-release-lines.yml").read_text(encoding="utf-8")
    pending_loop = content.split("for (const pr of pending)", 1)[1].split(
        "for (const pr of stranded)", 1
    )[0]
    stranded_loop = content.split("for (const pr of stranded)", 1)[1].split(
        "if (migrationErrors.length", 1
    )[0]

    assert "try {" in pending_loop
    assert "catch (error)" in pending_loop
    assert "migrationErrors.push" in pending_loop
    assert "try {" in stranded_loop
    assert "catch (error)" in stranded_loop
    assert "migrationErrors.push" in stranded_loop
    assert 'migrationErrors.join("; ")' in content


def test_release_sources_are_utf8_without_mojibake() -> None:
    """Impede nova corrupção de acentos nos arquivos alterados do fluxo."""
    root = WORKFLOW.parents[2]
    names = [
        ".github/pull_request_template.md",
        ".github/workflows/release-please.yml",
        ".github/workflows/forward-port-release.yml",
        "utils/rotate_release_lines.py",
        "utils/prepare_release_line_config.py",
        "utils/test_prepare_release_line_config.py",
    ]
    for name in names:
        content = (root / name).read_bytes().decode("utf-8")
        for broken in ("\ufffd", "\u00c3\u00a7", "\u00c3\u00a3", "\u00c3\u0192"):
            assert broken not in content, name


def test_release_mutations_are_serialized_and_leased() -> None:
    """Protege as duas linhas que publicam em master contra gravações cruzadas."""
    content = (WORKFLOW.parent / "release-please.yml").read_text(encoding="utf-8")
    assert "group: release-please-${{ github.repository }}" in content
    assert 'git push --force-with-lease="refs/heads/$target:$target_sha"' in content
    assert 'git push --force-with-lease="refs/heads/$source:$source_sha"' in content
    assert "skip-github-release: true" in content
    assert '"${{ steps.release-line.outputs.maintenance_branch }}"' not in content


def test_release_rotation_workflow_declares_release_entrypoint() -> None:
    """Mantém a criação pós-publicação limitada à branch e aos milestones."""
    workflow = WORKFLOW.parent / "rotate-release-lines.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "release:" in content
    assert "types: [published]" in content
    assert "utils/rotate_release_lines.py" in content
    assert "github.rest.git.createRef" in content
    assert "issues.createMilestone" in content
    assert "RELEASE_PLEASE_TOKEN" in content
    assert "github-token: ${{ secrets.RELEASE_PLEASE_TOKEN }}" in content
    assert "compareCommits" in content
    assert "gh pr create" not in content


def test_release_rotation_verifies_tag_ancestry() -> None:
    """Impede criar uma linha de manutenção a partir de uma tag fora do desenvolvimento."""
    workflow = WORKFLOW.parent / "rotate-release-lines.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "DEVELOPMENT_BRANCH" in content
    assert "github.rest.repos.compareCommits" in content
    assert "base: releaseSha" in content
    assert "head: developmentRef.data.object.sha" in content
    assert '"ahead", "identical"' in content


def test_release_workflows_pin_github_script_to_node_24() -> None:
    """Garante que os workflows não reintroduzam a runtime Node.js 20 depreciada."""
    workflow_paths = [
        WORKFLOW.parent / "pr-checks-comment.yml",
        WORKFLOW.parent / "pr-checks.yml",
        WORKFLOW.parent / "pr-release-milestone.yml",
        WORKFLOW.parent / "rotate-release-lines.yml",
    ]

    for workflow in workflow_paths:
        content = workflow.read_text(encoding="utf-8")
        assert "actions/github-script@v8" in content
    assert "actions/github-script@v7" not in content


def test_release_rotation_uses_project_environment() -> None:
    """Garante que a rotação use o ambiente uv que contém Pydantic."""
    workflow = WORKFLOW.parent / "rotate-release-lines.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "./.github/actions/setup-uv" in content
    assert "uv run --project vectora python utils/rotate_release_lines.py" in content


def test_release_workflows_pin_ubuntu_runner() -> None:
    """Garante que os workflows de release não dependam do rótulo móvel do Ubuntu."""
    workflow_paths = [
        WORKFLOW.parent / "pr-release-milestone.yml",
        WORKFLOW.parent / "rotate-release-lines.yml",
        WORKFLOW.parent / "forward-port-release.yml",
    ]

    for workflow in workflow_paths:
        content = workflow.read_text(encoding="utf-8")
        assert "runs-on: ubuntu-24.04" in content
        assert "ubuntu-latest" not in content


def test_app_and_edge_workflows_cobrem_todas_as_linhas_de_manutencao() -> None:
    """Evita que uma nova linha release/* fique sem validação de CI."""
    for name in ("edge.yml", "vectora.yml"):
        content = (WORKFLOW.parent / name).read_text(encoding="utf-8")
        assert 'branches: [master, "release/**"]' in content


def test_app_release_is_built_before_tag_and_tags_do_not_start_the_app_pipeline() -> (
    None
):
    """A tag is emitted only after distribution; tags must not retrigger the app build."""
    content = (WORKFLOW.parent / "vectora.yml").read_text(encoding="utf-8")

    assert 'tags: ["v*"]' not in content
    assert (
        "contains(github.event.head_commit.message, 'chore(master): release ')"
        in content
    )
    assert "tag-release:" in content
    assert (
        "needs: [release-native, publish-update-channel, publish-gha-bot-cli]"
        in content
    )
    assert 'git push origin "$TAG"' in content
    assert "gh release create" in content
    assert "--publish never" in content

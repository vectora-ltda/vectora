"""Testes dos contratos dos workflows de promoção e release."""

from __future__ import annotations

import json
from pathlib import Path

WORKFLOW: Path = (
    Path(__file__).resolve().parents[2]
    / ".."
    / ".github"
    / "workflows"
    / "forward-port-release.yml"
).resolve()
CONFIG: Path = WORKFLOW.parent.parent / "release-lines.json"


def test_forward_port_workflow_promotes_only_release_into_master() -> None:
    """Protege a promoção unidirecional configurada, as tentativas e a branch confiável."""
    content = WORKFLOW.read_text(encoding="utf-8")

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert ".github/release-lines.json" in content
    assert '"release/**"' in content
    assert 'echo "enabled=false" >> "$GITHUB_OUTPUT"' in content
    assert (
        "github.ref_name == steps.release-lines.outputs.maintenance_branch" in content
    )
    assert 'git merge-base --is-ancestor "origin/$MAINTENANCE_BRANCH" HEAD' in content
    assert 'git merge --no-edit "origin/$MAINTENANCE_BRANCH"' in content
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
        'conflict_branch="sync/release-promotion-${MAINTENANCE_BRANCH//\\//-}-to-${DEVELOPMENT_BRANCH//\\//-}"'
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


def test_release_please_uses_trusted_config_for_branch_gate() -> None:
    """Protege o token do Release Please contra configuração controlada pela branch."""
    workflow = WORKFLOW.parent / "release-please.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "github.event.repository.default_branch" in content
    assert ".github/release-lines.json" in content
    assert '"release/**"' in content
    assert 'echo "enabled=false" >> "$GITHUB_OUTPUT"' in content
    assert "enabled=false" in content
    assert "RELEASE_PLEASE_TOKEN" in content


def test_release_rotation_workflow_declares_release_entrypoint() -> None:
    """Mantém o workflow do GitHub conectado à fronteira executável de rotação."""
    workflow = WORKFLOW.parent / "rotate-release-lines.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "release:" in content
    assert "types: [published]" in content
    assert "utils/rotate_release_lines.py" in content
    assert "release-lines.json" in content
    assert "gh pr create" in content
    assert "RELEASE_PLEASE_TOKEN" in content
    assert "compareCommits" in content
    migration = workflow.parent / "migrate-release-line-prs.yml"
    migration_content = migration.read_text(encoding="utf-8")
    assert "applyMigrationUpdates" in migration_content
    migration_helper = (
        workflow.parent.parent / "scripts" / "migrate_release_line_prs.js"
    )
    helper_content = migration_helper.read_text(encoding="utf-8")
    assert "api.pulls.update" in helper_content
    assert "api.issues.update" in helper_content
    assert "pull_request_target" in migration_content
    assert "migrate_release_line_prs.js" in migration_content
    assert "pull_request.base.sha" in migration_content
    assert "pull_request.merge_commit_sha" in migration_content
    assert "release-lines.current.json" in migration_content
    assert "release-lines.previous.json" in migration_content
    assert "cancel-in-progress: false" in migration_content
    assert "ROTATION_BODY" not in migration_content
    assert "releaseLineTransition" in helper_content
    assert '      - "**"' in migration_content
    assert (
        "context.payload.pull_request.base.ref !== config.development.branch"
        in migration_content
    )


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
        WORKFLOW.parent / "migrate-release-line-prs.yml",
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
        WORKFLOW.parent / "migrate-release-line-prs.yml",
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

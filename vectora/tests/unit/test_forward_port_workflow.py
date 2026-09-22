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
    assert 'conflict_branch="sync/release-promotion-${GITHUB_RUN_ID}"' in content
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
    assert "EXPECTED_RELEASE_MILESTONE" in content
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
    assert "pulls.update" in migration_content
    assert "issues.update" in migration_content
    assert "pull_request_target" in migration_content

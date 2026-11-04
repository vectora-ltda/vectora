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
    assert "GH_TOKEN: ${{ github.token }}" in content


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
    assert "github-token: ${{ github.token }}" in content
    assert "github-token: ${{ secrets.RELEASE_PLEASE_TOKEN }}" not in content
    assert "github.event.action == 'opened'" not in content
    assert "context.payload.pull_request.milestone?.title" in content
    assert 'core.setOutput("current_milestone", payloadMilestone)' in content
    assert "if: github.event.pull_request != null" in content
    assert "if: github.event.pull_request.milestone == null" not in content


def test_stable_sync_skips_until_development_branch_exists() -> None:
    """A publicação não falha quando a rotação ainda está criando a branch nova."""
    content = (WORKFLOW.parent / "forward-port-release.yml").read_text(encoding="utf-8")

    assert (
        'git ls-remote --exit-code --heads origin "refs/heads/$DEVELOPMENT_BRANCH"'
        in content
    )
    assert '[[ "$status" -eq 2 ]]' in content
    assert "release rotation will seed it" in content


def test_release_please_uses_trusted_config_for_branch_gate() -> None:
    """Protege o token do Release Please contra configuração controlada pela branch."""
    workflow = WORKFLOW.parent / "release-please.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "github.event.repository.default_branch" in content
    assert "startsWith(github.ref, 'refs/tags/v')" in content
    assert (
        "RELEASE_TARGET_BRANCH: ${{ steps.release-line.outputs.release_target_branch }}"
        in content
    )
    assert ".github/release-lines.json" in content
    assert '"release/**"' in content
    assert 'echo "enabled=false" >> "$GITHUB_OUTPUT"' in content
    assert "enabled=false" in content
    assert "secrets.RELEASE_PLEASE_TOKEN" not in content
    assert "config_file" in content
    assert "manifest_file" in content
    assert "map(select(\\" not in content


def test_release_please_scopes_pr_body_to_current_release_notes() -> None:
    """Garante que o PR não publique o histórico inteiro do changelog."""
    workflow = WORKFLOW.parent / "release-please.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "utils/prepare_release_pr_body.py" in content
    assert "vectora/CHANGELOG.md" in content
    assert 'gh pr edit "$existing" --repo "$GITHUB_REPOSITORY"' in content
    assert '--body-file "$RUNNER_TEMP/release-pr-body.md"' in content


def test_release_please_requires_candidates_and_uses_canonical_branches() -> None:
    """Evita releases vazias e exige a branch canônica da linha."""
    workflow = WORKFLOW.parent / "release-please.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "Check for merged release candidates" in content
    assert "git fetch --tags --force origin" in content
    assert "--json milestone,labels,mergedAt" in content
    assert "fromdateiso8601" in content
    assert "latest_tag_epoch" in content
    assert "steps.release-candidates.outputs.has_candidates == 'true'" in content
    assert 'gh pr create --repo "$GITHUB_REPOSITORY"' in content
    assert "RELEASE_BRANCH: ${{ steps.release-plan.outputs.branch }}" in content
    assert 'headRefName == f"release/{suffix}"' not in content
    assert "googleapis/release-please-action" not in content
    assert (
        'git remote set-url origin "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"'
        in content
    )


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
    assert (
        'git push --force-with-lease="refs/heads/$RELEASE_BRANCH:$expected_sha"'
        in content
    )
    assert 'git push origin "HEAD:$RELEASE_BRANCH"' in content
    assert 'git push --force-with-lease="refs/heads/$target:$target_sha"' not in content
    assert 'git push --force-with-lease="refs/heads/$source:$source_sha"' not in content
    assert "skip-github-release: true" not in content
    assert '"${{ steps.release-line.outputs.maintenance_branch }}"' not in content


def test_release_automation_uses_current_line_tags_and_manual_dispatch() -> None:
    """Tags from old release lines cannot rewind the version or stall automation."""
    content = (WORKFLOW.parent / "release-please.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in content
    assert "github.event_name == 'workflow_dispatch'" in content
    assert '"v${release_major_minor}.*"' in content
    assert '"v*" --sort=-version:refname' not in content
    assert "chore(master): release $manifest_version" in content
    assert "prepare_release_line_config.py" in content
    assert (
        ".release-please-manifest.maintenance.json .github/release-lines.json"
        in content
    )


def test_release_pr_uses_maintenance_milestone_for_master_base() -> None:
    """A release PR to master must satisfy the trusted base-line milestone gate."""
    content = (WORKFLOW.parent / "release-please.yml").read_text(encoding="utf-8")
    assert 'pr_milestone="$maintenance_milestone"' in content
    assert (
        "RELEASE_MILESTONE: ${{ steps.release-line.outputs.pr_milestone }}" in content
    )


def test_release_pipeline_dispatches_rotation_after_github_token_release() -> None:
    """A tag created with GITHUB_TOKEN explicitly dispatches downstream workflows."""
    app = (WORKFLOW.parent / "vectora.yml").read_text(encoding="utf-8")
    rotation = (WORKFLOW.parent / "rotate-release-lines.yml").read_text(
        encoding="utf-8"
    )
    assert "actions: write" in app
    assert "gh workflow run rotate-release-lines.yml" in app
    assert "workflow_dispatch:" in rotation
    assert "release_tag" in rotation
    assert "gh workflow run release-please.yml" in rotation


def test_release_rotation_workflow_declares_release_entrypoint() -> None:
    """Mantém a criação pós-publicação limitada à branch e aos milestones."""
    workflow = WORKFLOW.parent / "rotate-release-lines.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "release:" in content
    assert "types: [published]" in content
    assert "utils/rotate_release_lines.py" in content
    assert "github.rest.git.createRef" in content
    assert "issues.createMilestone" in content
    assert "secrets.RELEASE_PLEASE_TOKEN" not in content
    assert "github-token: ${{ github.token }}" in content
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


def test_release_workflows_use_native_github_token() -> None:
    """Evita que um segredo expirado interrompa a criação automática das PRs."""
    workflows = tuple(WORKFLOW.parent.glob("*.yml"))
    assert workflows
    for workflow in workflows:
        content = workflow.read_text(encoding="utf-8")
        assert "secrets.RELEASE_PLEASE_TOKEN" not in content, workflow


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


def test_milestone_workflow_uses_trusted_workflow_revision() -> None:
    """Evita ler uma configuração rotacionada da branch de release da PR."""
    content = (WORKFLOW.parent / "pr-release-milestone.yml").read_text(encoding="utf-8")

    assert "ref: ${{ github.workflow_sha }}" in content
    assert "ref: ${{ github.event.pull_request.base.ref }}" not in content


def _trigger_block(content: str, event: str) -> str:
    """Devolve o bloco de um evento dentro de ``on:`` (até o próximo evento)."""
    on_block = "\n" + content.split("\non:\n", 1)[1].split("\nconcurrency:", 1)[0]
    after = on_block.split(f"\n  {event}:\n", 1)[1]
    return after.split("\n  workflow_dispatch:", 1)[0].split("\n  pull_request:", 1)[0]


def test_app_and_edge_workflows_validam_todo_pr_e_publicam_so_de_master() -> None:
    """Todo PR, qualquer que seja a base (inclusive PRs empilhados em feat/*),
    passa pelo pipeline completo; push e jobs de publicação seguem restritos às
    linhas de release."""
    for name in ("edge.yml", "vectora.yml"):
        content = (WORKFLOW.parent / name).read_text(encoding="utf-8")
        pull_request = _trigger_block(content, "pull_request")
        push = _trigger_block(content, "push")

        assert 'branches: ["**"]' in pull_request, name
        assert "master" not in pull_request, name
        assert 'branches: [master, "release/**"]' in push, name
        assert '"**"' not in push, name

    app = (WORKFLOW.parent / "vectora.yml").read_text(encoding="utf-8")
    edge = (WORKFLOW.parent / "edge.yml").read_text(encoding="utf-8")
    # These jobs only resolve metadata or build artifacts and must run on PRs
    # so the package matrix is validated before merge. Only publication and
    # tagging jobs are restricted to master.
    for job in ("release-channel:", "release-native:"):
        guard = app.split(f"\n  {job}\n", 1)[1].split("\n    steps:", 1)[0]
        assert "github.event_name == 'pull_request'" in guard, job

    for job in (
        "publish-update-channel:",
        "publish-gha-bot-cli:",
        "tag-release:",
    ):
        guard = app.split(f"\n  {job}\n", 1)[1].split("\n    steps:", 1)[0]
        assert "github.ref == 'refs/heads/master'" in guard, job
        assert "pull_request" not in guard, job
    deploy = edge.split("\n  deploy:\n", 1)[1].split("\n    steps:", 1)[0]
    assert "github.ref == 'refs/heads/master'" in deploy
    assert "pull_request" not in deploy


def test_native_release_builds_lancedb_from_source_on_unsupported_platforms() -> None:
    """Prepara Rust/protoc antes de sincronizar LanceDB sem wheel publicado."""
    content = (WORKFLOW.parent / "vectora.yml").read_text(encoding="utf-8")

    assert "actions-rust-lang/setup-rust-toolchain@v2" in content
    assert "toolchain: 1.97.0" in content
    assert "cache: false" in content
    assert "if: matrix.target == 'mac' && matrix.arch == 'x64'" in content
    assert "if: matrix.target == 'win' && matrix.arch == 'arm64'" in content
    assert "brew install protobuf" in content
    assert "choco install protoc --yes --no-progress" in content
    assert 'sync: "false"' in content
    assert "repository: lancedb/lancedb" in content
    assert "ref: b1b080b97c3edd2fd9f2aae8a3b71ff19487b1f8" in content
    assert "persist-credentials: false" in content
    assert "if ! cmp -s ../LICENSE LICENSE; then cp ../LICENSE LICENSE; fi" in content
    assert "Copy-Item ..\\LICENSE LICENSE" in content
    assert "uv build --wheel --out-dir ../../.lancedb-dist" in content
    assert "uv pip install --python vectora/.venv/bin/python" in content
    assert "uv pip install --python vectora/.venv/Scripts/python.exe" in content
    assert '"UV_NO_SYNC=1" >> "$GITHUB_ENV"' in content
    assert '"UV_NO_SYNC=1" >> $env:GITHUB_ENV' in content
    dependency = next(
        line
        for line in (WORKFLOW.parent.parent.parent / "vectora" / "pyproject.toml")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip().startswith('"lancedb>=')
    )
    assert "platform_machine != 'x86_64'" in dependency
    assert "platform_machine != 'ARM64'" in dependency
    local_transcription = next(
        line
        for line in (WORKFLOW.parent.parent.parent / "vectora" / "pyproject.toml")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip().startswith('"faster-whisper>=')
    )
    assert "platform_machine != 'x86_64'" in local_transcription
    assert "platform_machine != 'ARM64'" in local_transcription
    psycopg_binary = next(
        line
        for line in (WORKFLOW.parent.parent.parent / "vectora" / "pyproject.toml")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip().startswith('"psycopg-binary>=')
    )
    assert "platform_machine != 'ARM64'" in psycopg_binary
    qdrant_client = next(
        line
        for line in (WORKFLOW.parent.parent.parent / "vectora" / "pyproject.toml")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip().startswith('"qdrant-client>=')
    )
    assert "platform_machine != 'ARM64'" in qdrant_client


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

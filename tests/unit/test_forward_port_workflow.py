"""Testes dos contratos dos workflows de promoção e release."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path

import pytest

WORKFLOW: Path = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "forward-port-release.yml"
).resolve()
CONFIG: Path = WORKFLOW.parent.parent / "release-lines.json"


def test_setup_uv_defaults_to_the_repository_root() -> None:
    """O projeto Python é a raiz do repositório: um default apontando para
    uma subpasta quebra todo job que chama a action sem `working-directory`
    (o runner não consegue iniciar o processo no diretório inexistente)."""
    github = WORKFLOW.parent.parent
    action = (github / "actions" / "setup-uv" / "action.yml").read_text(
        encoding="utf-8"
    )

    block = action.split("  working-directory:\n", 1)[1].split("\nruns:", 1)[0]
    default = next(
        line.split("default:", 1)[1].strip().strip('"')
        for line in block.splitlines()
        if line.strip().startswith("default:")
    )

    assert default == "."
    assert (github.parent / default / "pyproject.toml").is_file()
    stale = [
        path.name
        for path in sorted((github / "workflows").glob("*.yml"))
        if "working-directory: vectora\n" in path.read_text(encoding="utf-8")
    ]
    assert stale == []


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
    assert "version_files" in content
    assert "manifest" not in content.lower()
    assert "map(select(\\" not in content


def test_release_please_scopes_pr_body_to_current_release_notes() -> None:
    """Garante que o PR não publique o histórico inteiro do changelog."""
    workflow = WORKFLOW.parent / "release-please.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "utils/release.py pr-body" in content
    assert "CHANGELOG.md" in content
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


@pytest.mark.parametrize("development", ["release/0.1.24", "release/0.2"])
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
        "utils/release.py",
        "utils/test_release.py",
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
    assert "chore(master): release $current_version" in content
    assert '--print-current-version --files "${version_files[@]}"' in content
    assert "utils/release.py prepare-line" in content
    assert (
        'git add .github/release-lines.json CHANGELOG.md "${version_files[@]}"'
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
    assert "utils/release.py rotate" in content
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
    assert "uv run python utils/release.py rotate" in content


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


PULL_REQUEST_WORKFLOWS = (
    "pr-checks.yml",
    "pr-labeler.yml",
    "pr-release-milestone.yml",
)


def test_pull_request_workflows_run_the_revision_of_the_pull_request() -> None:
    """Todo workflow de PR roda a versão do próprio PR: uma correção de CI no PR
    vale já nele, sem depender de a base receber o mesmo arquivo antes."""
    for name in PULL_REQUEST_WORKFLOWS:
        content = (WORKFLOW.parent / name).read_text(encoding="utf-8")

        assert "pull_request_target" not in content, name
        assert "workflow_run" not in content, name
        assert "github.workflow_sha" not in content, name
        assert _trigger_block(content, "pull_request"), name

    assert not (WORKFLOW.parent / "pr-checks-comment.yml").exists()


def test_pull_request_workflows_that_write_skip_forks() -> None:
    """Em PR de fork o token é somente leitura: o que escreve (label, milestone,
    comentário) só roda quando a cabeça do PR está neste repositório."""
    same_repo = "github.event.pull_request.head.repo.full_name == github.repository"
    labeler = (WORKFLOW.parent / "pr-labeler.yml").read_text(encoding="utf-8")
    milestone = (WORKFLOW.parent / "pr-release-milestone.yml").read_text(
        encoding="utf-8"
    )
    checks = (WORKFLOW.parent / "pr-checks.yml").read_text(encoding="utf-8")

    assert same_repo in labeler
    assert (
        same_repo
        in milestone.split("id: assign-milestone", 1)[1].split(
            "uses: actions/github-script", 1
        )[0]
    )
    comment = checks.split("\n  comment-no-plan-metadata:\n", 1)[1]
    assert same_repo in comment.split("\n    steps:", 1)[0]


def test_no_plan_metadata_comment_is_posted_by_a_separate_write_job() -> None:
    """O job que lê título e corpo do PR (dado não confiável) nunca recebe escrita:
    o comentário vem de um job à parte, com mensagem fixa e sem dado do PR."""
    checks = (WORKFLOW.parent / "pr-checks.yml").read_text(encoding="utf-8")
    validator = checks.split("\n  no-plan-metadata:\n", 1)[1].split(
        "\n  comment-no-plan-metadata:\n", 1
    )[0]
    comment = checks.split("\n  comment-no-plan-metadata:\n", 1)[1]

    assert "pull-requests: write" not in validator
    assert "upload-artifact" not in validator
    assert "needs: no-plan-metadata" in comment
    assert "pull-requests: write" in comment
    assert "github.event.pull_request.title" not in comment
    assert "github.event.pull_request.body" not in comment
    assert "createComment" in comment


def _trigger_block(content: str, event: str) -> str:
    """Devolve o bloco de um evento dentro de ``on:`` (até o próximo evento)."""
    on_block = "\n" + content.split("\non:\n", 1)[1].split("\nconcurrency:", 1)[0]
    after = on_block.split(f"\n  {event}:\n", 1)[1]
    return after.split("\n  workflow_dispatch:", 1)[0].split("\n  pull_request:", 1)[0]


def test_app_and_docs_workflows_validam_todo_pr_e_publicam_so_de_master() -> None:
    """Todo PR, qualquer que seja a base (inclusive PRs empilhados em feat/*),
    passa pelo pipeline completo; push e jobs de publicação seguem restritos às
    linhas de release."""
    for name in ("docs.yml", "vectora.yml"):
        content = (WORKFLOW.parent / name).read_text(encoding="utf-8")
        pull_request = _trigger_block(content, "pull_request")
        push = _trigger_block(content, "push")

        assert 'branches: ["**"]' in pull_request, name
        assert "master" not in pull_request, name
        assert 'branches: [master, "release/**"]' in push, name
        assert '"**"' not in push, name

    app = (WORKFLOW.parent / "vectora.yml").read_text(encoding="utf-8")
    docs = (WORKFLOW.parent / "docs.yml").read_text(encoding="utf-8")
    # These jobs only resolve metadata or build artifacts and must run on PRs
    # so the package matrix is validated before merge. Only publication and
    # tagging jobs are restricted to master.
    for job in ("release-channel:", "release-native:"):
        guard = app.split(f"\n  {job}\n", 1)[1].split("\n    steps:", 1)[0]
        assert "github.event_name == 'pull_request'" in guard, job

    for job in (
        "publish-update-channel:",
        "tag-release:",
    ):
        guard = app.split(f"\n  {job}\n", 1)[1].split("\n    steps:", 1)[0]
        assert "github.ref == 'refs/heads/master'" in guard, job
        assert "pull_request" not in guard, job
    deploy = docs.split("\n  deploy:\n", 1)[1].split("\n    steps:", 1)[0]
    assert "github.ref == 'refs/heads/master'" in deploy
    assert "pull_request" not in deploy


def test_native_release_uses_supported_python_architectures_for_backend_deps() -> None:
    """Usa x64 emulado no Windows ARM64, onde PyArrow não publica wheel."""
    content = (WORKFLOW.parent / "vectora.yml").read_text(encoding="utf-8")
    setup_uv = (
        WORKFLOW.parent.parent / "actions" / "setup-uv" / "action.yml"
    ).read_text(encoding="utf-8")
    assert "macos-26-intel" not in content
    assert "source-built LanceDB" not in content
    assert "Cache LanceDB Cargo build" not in content
    assert "PyArrow has no win_arm64" in content
    assert "Electron and the installer remain ARM64-native" in content
    assert (
        "architecture: ${{ matrix.target == 'win' && matrix.arch == 'arm64' && 'x64' || '' }}"
        in content
    )
    assert "architecture:" in setup_uv
    assert 'sync: "false"' in content
    lancedb_dependency = next(
        line
        for line in (WORKFLOW.parent.parent.parent / "pyproject.toml")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip().startswith('"lancedb>=')
    )
    assert "platform_machine != 'x86_64'" in lancedb_dependency
    assert "platform_machine != 'ARM64'" in lancedb_dependency
    local_transcription = next(
        line
        for line in (WORKFLOW.parent.parent.parent / "pyproject.toml")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip().startswith('"faster-whisper>=')
    )
    assert "platform_machine != 'x86_64'" in local_transcription
    assert "platform_machine != 'ARM64'" in local_transcription
    psycopg_binary = next(
        line
        for line in (WORKFLOW.parent.parent.parent / "pyproject.toml")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip().startswith('"psycopg-binary>=')
    )
    assert "platform_machine != 'ARM64'" in psycopg_binary
    qdrant_client = next(
        line
        for line in (WORKFLOW.parent.parent.parent / "pyproject.toml")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip().startswith('"qdrant-client>=')
    )
    assert "platform_machine != 'ARM64'" in qdrant_client


def test_linux_release_installs_native_package_build_tools() -> None:
    content = (WORKFLOW.parent / "vectora.yml").read_text(encoding="utf-8")

    assert "libarchive-tools" in content
    assert 'image="ghcr.io/canonical/snapcraft:8_core22"' in content
    assert "cat > \"$compat_dir/snapcraft\" <<'EOF'" in content
    assert '"${args[0]:-}" == "snap"' in content
    assert 'exec docker run --rm -v "$PWD:/project"' in content
    assert not any(
        line.lstrip().startswith("bash ") and ".sh" in line
        for line in content.splitlines()
    )
    assert "os: ubuntu-22.04" in content
    assert "os: ubuntu-22.04-arm" in content
    assert "sudo snap install lxd" not in content
    assert "sudo lxd init --auto" not in content
    assert "sudo snap install snapcraft" not in content
    assert "snapd" not in content


def test_linux_release_validates_and_uploads_electron_builder_artifact_names() -> None:
    """Confere que o fluxo Linux seleciona somente nomes de artefatos permitidos."""
    content = (WORKFLOW.parent / "vectora.yml").read_text(encoding="utf-8")

    for extension in ("AppImage", "deb", "rpm", "snap", "pacman", "flatpak"):
        assert f'"Vectora-*-linux-*.{extension}"' in content
        assert f"frontend/dist-electron/*.{extension}" in content

    assert '"Vectora-*-linux-*.tar.gz"' in content
    assert "frontend/dist-electron/*.tar.gz" in content
    assert "/artifacts/Vectora-*-linux-amd64.deb" in content
    assert "/artifacts/Vectora-*-linux-x86_64.rpm" in content
    assert "/artifacts/Vectora-*-linux-x64.pacman" in content


def test_nats_release_download_retries_transient_network_failures() -> None:
    """O download do sidecar não deve reprovar a matriz por reset transitório."""
    sconstruct = (WORKFLOW.parent.parent.parent / "SConstruct").read_text(
        encoding="utf-8"
    )

    assert "for attempt in range(1, 5):" in sconstruct
    assert "timeout=60" in sconstruct
    assert "(408, 429, 500, 502, 503, 504)" in sconstruct
    assert "time.sleep(2**attempt)" in sconstruct
    assert "os.replace(temporary_dest, dest)" in sconstruct


def test_app_release_is_built_before_tag_and_tags_do_not_start_the_app_pipeline() -> (
    None
):
    """A tag is emitted only after distribution; tags must not retrigger the app build."""
    content = (WORKFLOW.parent / "vectora.yml").read_text(encoding="utf-8")

    assert 'tags: ["v*"]' not in content
    # A decisão de publicar vem do job release-trigger (commit do Release Please
    # ou merge de PR da branch release/X.Y da série), nunca da mensagem solta.
    assert "release-trigger:" in content
    assert "contains(github.event.head_commit.message" not in content
    assert content.count("needs.release-trigger.outputs.is_release == 'true'") == 4
    assert "tag-release:" in content
    tag_job = content.split("  tag-release:", 1)[1].split(
        "\n  publish-update-channel:", 1
    )[0]
    needs = tag_job.split("if:", 1)[0]
    for dependency in ("release-native", "release-trigger", "desktop-install-smoke"):
        assert re.search(rf"\b{dependency}\b", needs), dependency
    # O manifesto do Cloudflare só pode apontar para arquivos que já existem na
    # release: o job que o publica depende do que cria a tag, e não o contrário.
    assert "publish-update-channel" not in needs
    publish_job = content.split("\n  publish-update-channel:\n", 1)[1]
    assert "tag-release" in publish_job.split("if:", 1)[0]
    assert "github.event_name == 'push'" in tag_job.split("runs-on:", 1)[0]
    assert "github.ref == 'refs/heads/master'" in tag_job.split("runs-on:", 1)[0]
    # Tag e release só passam pelo `gh`, para o job poder ser simulado.
    assert 'git push origin "$TAG"' not in content
    assert "git/refs" in tag_job
    assert "gh release create" in tag_job
    assert "--verify-tag" in tag_job
    assert "--publish never" in content


def test_release_attaches_only_the_selected_installers() -> None:
    """O passo de anexos anexava `find dist-release -type f`: a pasta inteira de
    runtime de um binário auxiliar entrava na lista e o `gh release upload`,
    que trata colchetes como glob, abortava o job. Só os instaladores escolhidos
    por nome, numa pasta própria, podem chegar ao checksum e ao upload."""
    content = (WORKFLOW.parent / "vectora.yml").read_text(encoding="utf-8")
    tag_job = content.split("\n  tag-release:\n", 1)[1]

    assert "find dist-release" not in tag_job
    stage = tag_job.index(
        "python3 utils/release_assets.py stage dist-release release-assets"
    )
    checksums = tag_job.index("python3 utils/sign_release_artifacts.py release-assets")
    upload = tag_job.index('gh release upload "$TAG"')
    assert stage < checksums < upload
    assert "sign_release_artifacts.py dist-release" not in tag_job
    assert "find release-assets -maxdepth 1 -type f -print0" in tag_job
    # Trava final: nome com caractere de glob nunca chega ao `gh release upload`.
    assert "[A-Za-z0-9._+-]+" in tag_job


def test_commitlint_packages_are_installed_with_exact_versions() -> None:
    """O job que comenta no PR não instala a última versão publicada a cada execução."""
    content = (WORKFLOW.parent / "pr-checks.yml").read_text(encoding="utf-8")

    assert re.search(r"@commitlint/cli@\d+\.\d+\.\d+(\s|$)", content)
    assert re.search(r"@commitlint/config-conventional@\d+\.\d+\.\d+(\s|$)", content)
    assert not re.search(r"@commitlint/[a-z-]+(\s|$)", content)


def test_release_build_injects_the_gateway_secret_from_github_secrets() -> None:
    """O segredo do gateway entra no instalador pelo build, vindo do secret do
    GitHub, e nunca por valor escrito no repositório."""
    content = (WORKFLOW.parent / "vectora.yml").read_text(encoding="utf-8")
    release_native = content.split("\n  release-native:\n", 1)[1].split(
        "\n  linux-package-install:\n", 1
    )[0]

    assert "VECTORA_APP_SECRET: ${{ secrets.VECTORA_APP_SECRET }}" in release_native
    assert ">> backend/defaults.env" in release_native
    inject = release_native.index(">> backend/defaults.env")
    assert inject < release_native.index("Build hybrid")


def test_gitleaks_runs_on_every_pull_request_with_a_pinned_version() -> None:
    """Varredura de segredos na árvore e nos commits do PR, com a ferramenta
    fixada em versão exata."""
    content = (WORKFLOW.parent / "gitleaks.yml").read_text(encoding="utf-8")

    assert "pull_request:" in content
    assert "fetch-depth: 0" in content
    assert "GITLEAKS_VERSION: v8." in content
    # O módulo do gitleaks declara o caminho antigo; o novo não instala.
    assert (
        "go install github.com/zricethezav/gitleaks/v8@${{ env.GITLEAKS_VERSION }}"
        in content
    )
    assert "gitleaks dir" in content
    assert "gitleaks git" in content
    assert "--redact" in content


def test_snyk_scans_resolve_their_prerequisites() -> None:
    """Os plugins npm e pip precisam das dependências de análise instaladas."""
    content = (WORKFLOW.parent / "snyk.yml").read_text(encoding="utf-8")
    node = content.split("\n  node-deps:\n", 1)[1].split("\n  python-deps:\n", 1)[0]
    python = content.split("\n  python-deps:\n", 1)[1].split("\n  code:\n", 1)[0]

    assert "pnpm install --frozen-lockfile --ignore-scripts" in node
    assert node.index("pnpm install") < node.index("snyk test")
    assert (
        "uv pip install --no-config --python .venv/bin/python --reinstall "
        '--no-deps pip "setuptools<81"' in python
    )


def test_milestone_workflow_resolves_the_release_line_of_stacked_prs() -> None:
    """Uma PR empilhada herda a linha de release da raiz da pilha: o workflow
    segue as bases pelas PRs abertas, atribui a milestone da raiz e repassa a
    raiz ao validador."""
    content = (WORKFLOW.parent / "pr-release-milestone.yml").read_text(encoding="utf-8")

    assert "github.rest.pulls.list" in content
    assert 'core.setOutput("stack_root", stackRoot)' in content
    assert (
        "CURRENT_STACK_ROOT_BASE: ${{ steps.assign-milestone.outputs.stack_root }}"
        in content
    )
    assert "head.repo?.full_name" in content
    # A busca é limitada e não entra em ciclo de bases.
    assert "seen.has(cursor)" in content


def test_labeler_keeps_labels_owned_by_path_rules() -> None:
    """A reconciliação pelo título não desfaz a label `ci` que as regras de
    caminho aplicam, e execuções do mesmo PR não rodam em paralelo."""
    labeler = (WORKFLOW.parent / "pr-labeler.yml").read_text(encoding="utf-8")

    assert 'const pathOwned = new Set(["ci"]);' in labeler
    assert "!pathOwned.has(label.name)" in labeler
    assert "concurrency:" in labeler
    assert "pr-labels-${{ github.event.pull_request.number }}" in labeler

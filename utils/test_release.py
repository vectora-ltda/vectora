"""Testes de utils/release.py, um bloco por subcomando."""

from __future__ import annotations

import io
import json
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import NotRequired, TypedDict

import pytest
import release
from release import (
    Commit,
    ReleaseLines,
    ReleasePullRequest,
    apply_plan,
    build_rotation_plan,
    calculate_plan,
    current_release_section,
    load_release_lines,
    prepare_body,
    read_current_version,
    rotation_for_release,
    select_release_pr,
    select_update_channel,
    selection_for_branch,
    update_release_line_files,
    write_rotated_config,
)

# ── line ─────────────────────────────────────────────────────────────────────


def test_configured_branches_enable_release_workflows() -> None:
    """Branches configuradas de desenvolvimento e manutenção habilitam os jobs de promoção."""
    config = load_release_lines()

    assert selection_for_branch(config.development.branch, config)["enabled"] == "true"
    assert selection_for_branch(config.maintenance.branch, config)["enabled"] == "true"


def test_selection_exposes_version_files_only_for_release_branches() -> None:
    """Os arquivos de versão vêm da própria configuração, não de manifestos."""
    config = load_release_lines()

    development = selection_for_branch(config.development.branch, config)
    maintenance = selection_for_branch(config.maintenance.branch, config)

    assert config.version_files
    assert development["version_files"] == " ".join(config.version_files)
    assert maintenance["version_files"] == development["version_files"]
    assert "config_file" not in development
    assert "manifest_file" not in development
    assert selection_for_branch("feat/untrusted", config)["version_files"] == ""


def test_unknown_branch_skips_release_workflows() -> None:
    """Branches de feature não podem habilitar workflows de release que usam token."""
    config = load_release_lines()

    assert selection_for_branch("feat/untrusted", config)["enabled"] == "false"


def test_empty_branch_skips_release_workflows() -> None:
    """Um nome de branch vazio não pode autorizar a automação de release."""
    config = load_release_lines()
    assert selection_for_branch("", config)["enabled"] == "false"


def test_missing_branch_skips_release_workflows() -> None:
    """A ausência do nome da branch não pode autorizar a automação de release."""
    config = load_release_lines()
    assert selection_for_branch(None, config)["enabled"] == "false"


def _write_config(path: Path, **overrides: object) -> Path:
    payload: dict[str, object] = {
        "version_files": ["package.json"],
        "development": {"branch": "release/0.2", "milestone": "0.2"},
        "maintenance": {"branch": "master", "milestone": "0.1.x"},
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_invalid_configuration_is_rejected(tmp_path: Path) -> None:
    """Linhas de release inválidas falham antes do uso pelo workflow."""
    path = tmp_path / "release-lines.json"

    assert load_release_lines(_write_config(path)).version_files == ("package.json",)

    same_branch = {"branch": "same", "milestone": "0.2"}
    _write_config(
        path,
        development=same_branch,
        maintenance={"branch": "same", "milestone": "0.1.x"},
    )
    with pytest.raises(ValueError, match="must differ"):
        load_release_lines(path)

    for bad_files in ([], ["../package.json"], ["/abs/package.json"], [""]):
        _write_config(path, version_files=bad_files)
        with pytest.raises(ValueError, match="invalid release-line configuration"):
            load_release_lines(path)

    _write_config(path, version_files=["package.json", "package.json"])
    with pytest.raises(ValueError, match="duplicated"):
        load_release_lines(path)

    _write_config(
        path,
        maintenance={
            "branch": "master",
            "milestone": "0.1.x",
            "release_please_manifest": "manifest.json",
        },
    )
    with pytest.raises(ValueError, match="invalid release-line configuration"):
        load_release_lines(path)


from release import normalize_changelog

# ── plan ─────────────────────────────────────────────────────────────────────


def test_feature_bumps_minor_before_one() -> None:
    plan = calculate_plan("0.3.0", [Commit(subject="feat: add canonical release PR")])
    assert plan.next_version == "0.4.0"
    assert plan.bump == "minor"


def test_fix_bumps_patch() -> None:
    plan = calculate_plan("1.2.3", [Commit(subject="fix: repair release branch")])
    assert plan.next_version == "1.2.4"


def test_breaking_change_uses_minor_before_one() -> None:
    plan = calculate_plan("0.3.0", [Commit(subject="feat!: change release contract")])
    assert plan.next_version == "0.4.0"


def test_breaking_change_footer_uses_major_after_one() -> None:
    plan = calculate_plan(
        "1.3.0",
        [
            Commit(
                subject="feat: change release contract",
                body="BREAKING CHANGE: callers must provide a workspace",
            )
        ],
    )
    assert plan.next_version == "2.0.0"
    assert plan.breaking_entries == ["feat: change release contract"]


def test_non_release_commits_do_not_create_plan() -> None:
    plan = calculate_plan("0.3.0", [Commit(subject="docs: explain release flow")])
    assert plan.next_version == "0.3.0"
    assert plan.bump == "none"


_LOCK_TEMPLATE = """version = 1

[[package]]
name = "pydantic"
version = "0.3.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "vectora"
version = "0.3.0"
source = { editable = "." }
dependencies = [
    { name = "pydantic" },
]
"""


def test_apply_updates_all_configured_versions_and_lockfile(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text('version = "0.3.0"\n', encoding="utf-8")
    frontend_package = tmp_path / "frontend" / "package.json"
    frontend_package.parent.mkdir()
    frontend_package.write_text(json.dumps({"version": "0.3.0"}), encoding="utf-8")
    lock_path = tmp_path / "uv.lock"
    lock_path.write_text(_LOCK_TEMPLATE, encoding="utf-8")
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n", encoding="utf-8")
    plan = calculate_plan("0.3.0", [Commit(subject="fix: repair release")])
    apply_plan(tmp_path, plan, ["pyproject.toml", "frontend/package.json", "uv.lock"])
    assert lock_path.read_text(encoding="utf-8") == _LOCK_TEMPLATE.replace(
        'name = "vectora"\nversion = "0.3.0"', 'name = "vectora"\nversion = "0.3.1"'
    )
    assert '"version": "0.3.1"' in frontend_package.read_text(encoding="utf-8")
    assert changelog.read_text(encoding="utf-8").startswith("## [0.3.1]")
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "CHANGELOG.md",
        "frontend",
        "pyproject.toml",
        "uv.lock",
    ]

    without_package = _LOCK_TEMPLATE.replace('name = "vectora"', 'name = "other"')
    lock_path.write_text(without_package, encoding="utf-8")
    with pytest.raises(ValueError, match="vectora"):
        apply_plan(tmp_path, plan, ["uv.lock"])
    assert lock_path.read_text(encoding="utf-8") == without_package

    duplicated = _LOCK_TEMPLATE.replace('name = "pydantic"', 'name = "vectora"')
    lock_path.write_text(duplicated, encoding="utf-8")
    with pytest.raises(ValueError, match="vectora"):
        apply_plan(tmp_path, plan, ["uv.lock"])
    assert lock_path.read_text(encoding="utf-8") == duplicated


def test_current_version_comes_from_first_version_file(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"version": "0.3.0"}', encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "1.2.3"\n', encoding="utf-8"
    )

    assert read_current_version(tmp_path, ["pyproject.toml", "package.json"]) == "1.2.3"
    assert read_current_version(tmp_path, ["package.json"]) == "0.3.0"

    (tmp_path / "package.json").write_text('{"name": "sem-versao"}', encoding="utf-8")
    with pytest.raises(ValueError, match=r"package\.json"):
        read_current_version(tmp_path, ["package.json"])
    (tmp_path / "package.json").write_text('{"version": "v1"}', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid semantic version"):
        read_current_version(tmp_path, ["package.json"])
    with pytest.raises(ValueError, match="unsupported version source"):
        read_current_version(tmp_path, ["uv.lock"])
    with pytest.raises(ValueError, match="at least one"):
        read_current_version(tmp_path, [])


def test_cli_prints_current_version_without_commits(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.3.0"\n', encoding="utf-8"
    )
    script = Path(__file__).with_name("release.py")
    args = [
        sys.executable,
        str(script),
        "plan",
        "--root",
        str(tmp_path),
        "--files",
        "pyproject.toml",
    ]

    ok = subprocess.run(  # noqa: S603  # nosec B603
        [*args, "--print-current-version"], capture_output=True, text=True, check=False
    )
    assert ok.returncode == 0
    assert ok.stdout.strip() == "0.3.0"

    missing_commits = subprocess.run(  # noqa: S603  # nosec B603
        args, capture_output=True, text=True, check=False
    )
    assert missing_commits.returncode == 2
    assert "--commits-json" in missing_commits.stderr


# ── prepare-line ─────────────────────────────────────────────────────────────


def _write_inputs(tmp_path: Path, version: str = "0.3.0") -> tuple[Path, Path]:
    """Prepara arquivos temporários para testar a migração da configuração."""
    changelog = tmp_path / "CHANGELOG.md"
    lines = tmp_path / "release-lines.json"
    changelog.write_text(f"## [{version}]\n\nAtual release.\n", encoding="utf-8")
    lines.write_text(
        json.dumps(
            {
                "version_files": ["package.json"],
                "development": {"branch": "release/0.3", "milestone": "0.3"},
                "maintenance": {"branch": "master", "milestone": "0.2.x"},
            }
        ),
        encoding="utf-8",
    )
    return changelog, lines


def test_minor_release_updates_next_lines(tmp_path: Path) -> None:
    """A minor prepara a próxima linha e a manutenção da versão publicada."""
    changelog, lines = _write_inputs(tmp_path)

    assert update_release_line_files(changelog, lines, "release/0.3") is True
    assert json.loads(lines.read_text(encoding="utf-8")) == {
        "version_files": ["package.json"],
        "development": {"branch": "release/0.4", "milestone": "0.4"},
        "maintenance": {"branch": "master", "milestone": "0.3.x"},
    }
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "CHANGELOG.md",
        "release-lines.json",
    ]


def test_patch_release_does_not_rotate_lines(tmp_path: Path) -> None:
    """Uma versão patch, ou de outra branch, preserva as linhas existentes."""
    changelog, lines = _write_inputs(tmp_path, "0.3.1")
    before = lines.read_text(encoding="utf-8")

    assert update_release_line_files(changelog, lines, "release/0.3") is False
    assert lines.read_text(encoding="utf-8") == before

    changelog, lines = _write_inputs(tmp_path)
    assert update_release_line_files(changelog, lines, "master") is False
    assert lines.read_text(encoding="utf-8") == before


# ── rotate ───────────────────────────────────────────────────────────────────


CONFIG = ReleaseLines(
    version_files=("package.json",),
    development={"branch": "release/0.3", "milestone": "0.3"},
    maintenance={"branch": "master", "milestone": "0.2.x"},
)


def test_minor_release_opens_next_release_branch() -> None:
    """Publicar a minor mantém master estável e abre a próxima minor."""
    rotation = rotation_for_release("v0.3.0", CONFIG, "master")
    assert rotation is not None
    assert rotation["development_branch"] == "release/0.4"
    assert rotation["development_milestone"] == "0.4"
    assert rotation["maintenance_branch"] == "master"
    assert rotation["maintenance_milestone"] == "0.3.x"


def test_release_on_development_branch_does_not_rotate() -> None:
    """Uma publicação feita na origem de desenvolvimento não roda a rotação."""
    assert rotation_for_release("v0.3.0", CONFIG, "release/0.3") is None


def test_patch_release_does_not_rotate() -> None:
    """Patches não abrem outra linha de desenvolvimento."""
    assert rotation_for_release("v0.3.1", CONFIG, "master") is None


def test_rotation_plan_moves_open_minor_prs() -> None:
    """Move somente PRs da minor anterior, preservando patches em master."""
    rotation = rotation_for_release("v0.3.0", CONFIG, "master")
    assert rotation is not None
    plan = build_rotation_plan(
        rotation,
        [
            {"number": 12, "base_branch": "release/0.3", "milestone": "0.3"},
            {"number": 13, "base_branch": "master", "milestone": "0.3.x"},
        ],
    )
    assert {item["kind"] for item in plan} == {
        "create_branch",
        "ensure_milestone",
        "update_base",
        "update_milestone",
        "publish_config",
    }
    assert {item["value"] for item in plan if item["kind"] == "update_base"} == {
        "release/0.4"
    }
    assert not any(item["pull_request"] == 13 for item in plan)
    assert [item for item in plan if item["kind"] == "update_milestone"] == [
        {"kind": "update_milestone", "pull_request": 12, "value": "0.4"}
    ]


def test_rotation_writes_next_config(tmp_path: Path) -> None:
    """Persiste as novas linhas e preserva os arquivos de versão."""
    rotation = rotation_for_release("v0.3.0", CONFIG, "master")
    assert rotation is not None
    path = tmp_path / "release-lines.json"
    write_rotated_config(path, CONFIG, rotation)
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["development"]["branch"] == "release/0.4"
    assert result["development"]["milestone"] == "0.4"
    assert result["maintenance"]["branch"] == "master"
    assert result["maintenance"]["milestone"] == "0.3.x"
    assert result["version_files"] == ["package.json"]


@pytest.mark.parametrize("target", [None, "", "develop", "release/0.3"])
def test_invalid_target_does_not_rotate(target: str | None) -> None:
    """Uma origem não autorizada não gera operações de rotação."""
    assert rotation_for_release("v0.3.0", CONFIG, target) is None


def test_commit_target_can_rotate() -> None:
    """O workflow verifica ancestralidade quando o evento aponta para um SHA."""
    assert rotation_for_release("v0.3.0", CONFIG, "a" * 40) is not None


@pytest.mark.parametrize("tag", ["", "v0.1.0", "v0.3.1", "invalid", "v00.3.0"])
def test_unrelated_tags_do_not_rotate(tag: str) -> None:
    """Tags antigas, patches e valores inválidos não avançam a configuração."""
    assert rotation_for_release(tag, CONFIG, "master") is None


def test_newer_release_requires_configuration() -> None:
    """Uma minor futura falha explicitamente até a configuração ser atualizada."""
    with pytest.raises(ValueError, match="newer than configured"):
        rotation_for_release("v0.4.0", CONFIG, "master")


def test_already_rotated_preserves_previous_line(tmp_path: Path) -> None:
    """Reexecutar a rotação continua identificando PRs da minor publicada."""
    rotation = rotation_for_release("v0.3.0", CONFIG, "master")
    assert rotation is not None
    path = tmp_path / "lines.json"
    write_rotated_config(path, CONFIG, rotation)
    config = ReleaseLines.model_validate_json(path.read_bytes())
    repeated = rotation_for_release("v0.3.0", config, "master")
    assert repeated is not None
    assert repeated["previous_development_branch"] == "release/0.3"
    assert repeated["previous_development_milestone"] == "0.3"
    assert repeated["development_branch"] == "release/0.4"


def test_incomplete_rotation_is_not_accepted() -> None:
    """Uma linha avançada sem atualizar a manutenção não é rotação concluída."""
    config = CONFIG.model_copy(
        update={
            "development": CONFIG.development.model_copy(
                update={
                    "branch": "release/0.4",
                    "milestone": "0.4",
                }
            ),
        }
    )
    assert rotation_for_release("v0.3.0", config, "master") is None


@pytest.mark.parametrize(
    "payload", ["{}", "[]", "null", '{"release": []}', '{"release": {}}', "{"]
)
def test_main_reports_invalid_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    payload: str,
) -> None:
    """Entradas inválidas retornam diagnóstico controlado, sem traceback."""
    event = tmp_path / "event.json"
    event.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["release.py", str(event)])
    assert release.rotate_main() == 1
    assert "invalid release event" in capsys.readouterr().err


@pytest.mark.parametrize("write", [False, True])
def test_main_valid_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    write: bool,
) -> None:
    """O CLI emite saídas e só grava a configuração quando solicitado."""
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"release": {"tag_name": "v0.3.0", "target_commitish": "master"}}),
        encoding="utf-8",
    )
    config = tmp_path / "lines.json"
    config.write_text(CONFIG.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(release, "load_release_lines", lambda _: CONFIG)
    argv = ["release.py", str(event)]
    if write:
        argv.extend(["--write", str(config)])
    monkeypatch.setattr(sys, "argv", argv)
    assert release.rotate_main() == 0
    assert "rotate=true" in capsys.readouterr().out
    assert json.loads(config.read_text(encoding="utf-8"))["development"]["branch"] == (
        "release/0.4" if write else "release/0.3"
    )


def test_main_patch_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Um patch válido termina com sucesso sem produzir uma rotação."""
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"release": {"tag_name": "v0.3.1", "target_commitish": "master"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["release.py", str(event)])
    assert release.rotate_main() == 0
    assert capsys.readouterr().out.strip() == "rotate=false"


def test_main_accepts_workflow_dispatch_release_tag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Workflow dispatch recovers rotation when GITHUB_TOKEN suppresses release events."""
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"inputs": {"release_tag": "v0.3.0", "target_branch": "master"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(release, "load_release_lines", lambda _: CONFIG)
    monkeypatch.setattr(sys, "argv", ["release.py", str(event)])

    assert release.rotate_main() == 0
    output = capsys.readouterr().out
    assert "release_tag=v0.3.0" in output
    assert "rotate=true" in output


# ── select-pr ────────────────────────────────────────────────────────────────


def _pr(version: str) -> ReleasePullRequest:
    """Cria uma resposta do CLI para a minor ou o patch em master."""
    minor = version.endswith(".0")
    suffix = "0.3" if minor else version
    return ReleasePullRequest.model_validate(
        {
            "number": 3 if minor else 20,
            "baseRefName": "master",
            "headRefName": f"release/{suffix}",
            "headRefOid": "a" * 40,
            "headRepository": {"nameWithOwner": "vectora-ltda/vectora"},
            "labels": [{"name": "autorelease: pending"}],
            "title": f"chore(master): release {version}",
        }
    )


@pytest.mark.parametrize("minor", [False, True])
def test_parallel_lines_are_isolated(minor: bool) -> None:
    """A PR mais recente do patch não é selecionada durante a minor e vice-versa."""
    prs = [
        _pr("0.3.0"),
        _pr("0.2.1"),
    ]
    result = select_release_pr(
        prs,
        repository="vectora-ltda/vectora",
        source="release/0.3" if minor else "master",
        target="master",
        milestone="0.3" if minor else "0.2.x",
    )
    assert result == prs[0 if minor else 1]


@pytest.mark.parametrize(
    "field,value",
    [
        ("baseRefName", "release/0.2"),
        ("headRefName", "release-0.3-lookalike"),
        ("headRepository", {"nameWithOwner": "attacker/vectora"}),
        ("headRepository", None),
        ("labels", []),
        ("title", "chore(master): release 0.4.0"),
    ],
)
def test_untrusted_pr_is_ignored(field: str, value: object) -> None:
    """Nome parecido, fork ou metadados divergentes nunca autorizam mutações."""
    data = _pr("0.3.0").model_dump()
    data[field] = value
    pr = ReleasePullRequest.model_validate(data)
    assert (
        select_release_pr(
            [pr],
            repository="vectora-ltda/vectora",
            source="release/0.3",
            target="master",
            milestone="0.3",
        )
        is None
    )


def test_duplicate_release_prs_fail_closed() -> None:
    """Duas PRs válidas da mesma linha exigem resolver a ambiguidade."""
    with pytest.raises(ValueError, match="ambiguous"):
        select_release_pr(
            [_pr("0.2.1"), _pr("0.2.2")],
            repository="vectora-ltda/vectora",
            source="master",
            target="master",
            milestone="0.2.x",
        )


def test_noncanonical_release_head_is_not_a_versioned_release_pr() -> None:
    """Somente a branch canônica da linha pode virar a PR oficial."""
    pr = _pr("0.3.0").model_copy(update={"headRefName": "release-0.3"})
    assert (
        select_release_pr(
            [pr],
            repository="vectora-ltda/vectora",
            source="release/0.3",
            target="master",
            milestone="0.3",
        )
        is None
    )


def test_cli_rejects_malformed_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """JSON inválido interrompe o pipeline antes de alterar branches."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release.py select-pr",
            "--repository",
            "vectora-ltda/vectora",
            "--source",
            "master",
            "--target",
            "master",
            "--milestone",
            "0.2.x",
        ],
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert release.select_pr_main() == 1
    assert "Invalid release PR selection" in capsys.readouterr().err


# ── pr-body ──────────────────────────────────────────────────────────────────


def test_keeps_only_the_first_release_section() -> None:
    """Mantém somente a seção da release atual no corpo da PR."""
    notes = """:robot: release
---

## [0.2.0](https://example.test/0.2)

### Features

* current ([#1](https://example.test/issues/1))

## [0.1.23](https://example.test/0.1)

* historical
"""

    body = prepare_body(notes)

    assert "0.2.0" in body
    assert "current" in body
    assert "historical" not in body


def test_compacts_links_when_current_section_is_too_large() -> None:
    """Remove URLs repetitivas antes de recorrer ao resumo curto."""
    notes = "## [0.2.0](https://example.test/release)\n\n" + "\n".join(
        f"* change {index} ([commit](https://example.test/commits/{index}))"
        for index in range(100)
    )

    body = prepare_body(notes, max_body_bytes=2_500)

    assert "change 99" in body
    assert "https://example.test/release" in body
    assert "https://example.test/commits/" not in body
    assert len(body.encode("utf-8")) <= 2_500


def test_rejects_notes_without_a_release_heading() -> None:
    """Rejeita notas que não identificam uma seção de release."""
    with pytest.raises(ValueError, match="release heading"):
        current_release_section("no release here")


def test_drops_changelog_document_header() -> None:
    """Não duplica o título geral do changelog no corpo da PR."""
    body = prepare_body("# Changelog\n\n## [0.2.0]\n\n### Features\n\n* current\n")

    assert body.startswith("## [0.2.0]")
    assert "# Changelog" not in body


def test_strips_process_metadata_from_release_entries() -> None:
    """Remove metadados de planejamento e revisão das entradas publicadas."""
    notes = """## [0.2.0]

### Features

* add board drawer (Fase 7 - drawer) (abc1234)
* add board switcher (Sprint 4) (def5678)
* add native client (CodeRabbit review) (ghi9012)
"""

    body = prepare_body(notes)

    assert "Fase" not in body
    assert "Sprint" not in body
    assert "CodeRabbit" not in body
    assert "add board drawer (abc1234)" in body


def test_summarizes_release_when_compacted_notes_still_exceed_limit() -> None:
    """Usa um resumo mínimo quando o changelog continua grande após compactação."""
    notes = "## [0.2.0]\n\n### Features\n\n" + "\n".join(
        f"* change {index}" for index in range(100)
    )

    body = prepare_body(notes, max_body_bytes=200)

    assert body == (
        "## [0.2.0]\n\nThe complete release notes are maintained in `CHANGELOG.md`.\n"
    )


# ── update-channel ───────────────────────────────────────────────────────────


def test_maintenance_version_selects_maintenance() -> None:
    assert select_update_channel("0.1.23") == "maintenance"


@pytest.mark.parametrize("version", ["0.2.0", "1.0.0"])
def test_current_minor_versions_select_latest(version: str) -> None:
    assert select_update_channel(version) == "latest"


@pytest.mark.parametrize("version", ["", "0.1", "v0.1.23", "0.1.x"])
def test_missing_or_malformed_versions_fail(version: str) -> None:
    with pytest.raises(ValueError, match="Versão do pacote inválida"):
        select_update_channel(version)


def test_null_version_fails_with_controlled_error() -> None:
    with pytest.raises(ValueError, match="Versão do pacote inválida"):
        select_update_channel(None)


# ── validate-milestone ───────────────────────────────────────────────────────


class RepositoryPayload(TypedDict):
    full_name: NotRequired[str | None]


class HeadPayload(TypedDict):
    ref: NotRequired[str]
    repo: NotRequired[RepositoryPayload | None]


class BasePayload(TypedDict):
    ref: NotRequired[str]


class MilestonePayload(TypedDict):
    title: NotRequired[str]


class LabelPayload(TypedDict):
    name: NotRequired[str]


class PullRequestPayload(TypedDict):
    base: NotRequired[BasePayload]
    head: NotRequired[HeadPayload]
    title: NotRequired[str]
    milestone: NotRequired[MilestonePayload | None]
    labels: NotRequired[list[LabelPayload]]


class RepositoryEventPayload(TypedDict):
    full_name: NotRequired[str]


class PullRequestEvent(TypedDict):
    repository: NotRequired[RepositoryEventPayload]
    pull_request: NotRequired[PullRequestPayload | None]


_ACTIVE_LINES = release.load_release_lines()
DEVELOPMENT_BRANCH: str = _ACTIVE_LINES.development.branch
DEVELOPMENT_MILESTONE: str = _ACTIVE_LINES.development.milestone
MAINTENANCE_BRANCH: str = _ACTIVE_LINES.maintenance.branch
MAINTENANCE_MILESTONE: str = _ACTIVE_LINES.maintenance.milestone


def _event(
    *,
    base: str,
    milestone: str | None,
    head: str = "feature/example",
    labels: list[str] | None = None,
    head_repo: str | None = "vectora-ltda/vectora",
    title: str = "fix: routine maintenance",
) -> PullRequestEvent:
    """Monta um evento mínimo de pull request para os testes do validador."""
    return {
        "repository": {"full_name": "vectora-ltda/vectora"},
        "pull_request": {
            "base": {"ref": base},
            "head": {"ref": head, "repo": {"full_name": head_repo}},
            "title": title,
            "milestone": {"title": milestone} if milestone else None,
            "labels": [{"name": label} for label in labels or []],
        },
    }


def test_empty_event_is_accepted() -> None:
    """Aceita eventos sem pull request para manter o webhook tolerante."""
    assert release.validate_pull_request({}) == []


def test_main_rejects_malformed_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retorna erro quando o arquivo de evento não segue o contrato."""
    event_path = tmp_path / "event.json"
    event_path.write_text('{"pull_request": "malformed"}', encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))

    assert release.validate_milestone_main() == 2


def test_main_uses_milestone_assigned_during_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valida a milestone atualizada em vez do snapshot obsoleto do webhook."""
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(_event(base=MAINTENANCE_BRANCH, milestone=None)),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("CURRENT_RELEASE_MILESTONE", DEVELOPMENT_MILESTONE)
    monkeypatch.setenv("CURRENT_PR_BASE", DEVELOPMENT_BRANCH)

    assert release.validate_milestone_main() == 0


def test_main_preserves_event_milestone_when_workflow_output_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Não apaga a milestone correta quando o step de atribuição é pulado."""
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(_event(base=MAINTENANCE_BRANCH, milestone=MAINTENANCE_MILESTONE)),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("CURRENT_RELEASE_MILESTONE", "")
    monkeypatch.setenv("CURRENT_PR_BASE", "")

    assert release.validate_milestone_main() == 0


def test_main_preserves_event_head_repository_when_ref_output_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mantém a identidade validada quando o workflow só fornece a ref."""
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            _event(
                base="stack/base-contracts",
                milestone=None,
                head="stack/browser-editor",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("CURRENT_PR_HEAD_REF", "stack/browser-editor")
    monkeypatch.setenv("CURRENT_PR_HEAD_REPO", "")

    assert release.validate_milestone_main() == 0


def test_main_uses_github_repository_when_event_repository_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aceita uma base interna quando o payload confiável omite repository."""
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "repository": {},
                "pull_request": {
                    "base": {"ref": "feat/llm-workbench-integration"},
                    "head": {"ref": "feat/llm-workbench-integration-review"},
                    "milestone": None,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "vectora-ltda/vectora")
    monkeypatch.setenv("CURRENT_PR_HEAD_REF", "feat/llm-workbench-integration-review")
    monkeypatch.setenv("CURRENT_PR_HEAD_REPO", "vectora-ltda/vectora")

    assert release.validate_milestone_main() == 0


def test_master_feature_pr_is_accepted() -> None:
    """Aceita uma feature na base de desenvolvimento com sua milestone."""
    assert (
        release.validate_pull_request(
            _event(base=DEVELOPMENT_BRANCH, milestone=DEVELOPMENT_MILESTONE)
        )
        == []
    )


def test_stacked_feature_pr_is_accepted_without_release_milestone() -> None:
    """Aceita PRs empilhadas apenas dentro do namespace controlado de stack."""
    assert (
        release.validate_pull_request(
            _event(base="stack/base-contracts", milestone=None)
        )
        == []
    )


def test_contracts_stack_base_is_accepted_without_release_milestone() -> None:
    """Aceita o primeiro PR empilhado diretamente sobre os contratos."""
    assert (
        release.validate_pull_request(
            _event(base="feat/contratos-workbenches", milestone=None)
        )
        == []
    )


@pytest.mark.parametrize("head_repo", ["fork/vectora", None])
def test_stacked_fork_pr_does_not_bypass_release_milestone(
    head_repo: str | None,
) -> None:
    """Somente PRs do próprio repositório podem usar a isenção de stack."""
    errors = release.validate_pull_request(
        _event(base="stack/base-contracts", milestone=None, head_repo=head_repo)
    )
    assert errors
    assert "base declarada" in errors[0]


@pytest.mark.parametrize(
    "base",
    [
        "stack/base-contracts",
        "feat/contratos-workbenches",
        "feat/llm-workbench-integration",
    ],
)
def test_contract_stack_fork_does_not_bypass_release_milestone(base: str) -> None:
    """Bases empilhadas não liberam PRs de forks."""
    errors = release.validate_pull_request(
        _event(base=base, milestone=None, head_repo="fork/vectora")
    )
    assert errors and "base declarada" in errors[0]


def test_stack_head_from_same_owner_different_repository_does_not_bypass() -> None:
    """Não confunde outro repositório da mesma organização com o repositório da PR."""
    errors = release.validate_pull_request(
        _event(
            base="stack/base-contracts",
            milestone=None,
            head_repo="vectora-ltda/other-repository",
        )
    )
    assert errors and "base declarada" in errors[0]


def test_master_pr_rejects_maintenance_milestone() -> None:
    """Rejeita milestone de manutenção em uma PR de desenvolvimento."""
    errors = release.validate_pull_request(
        _event(base=DEVELOPMENT_BRANCH, milestone=MAINTENANCE_MILESTONE)
    )
    assert errors and DEVELOPMENT_MILESTONE in errors[0]


def test_scoped_vext_pr_accepts_minor_milestone() -> None:
    """Aceita uma PR VEXT com a milestone da próxima minor."""
    event = _event(
        base=DEVELOPMENT_BRANCH,
        milestone=DEVELOPMENT_MILESTONE,
        title="feat(vext): build ecosystem artifacts",
    )
    assert release.validate_pull_request(event) == []


def test_vext_pr_rejects_maintenance_milestone() -> None:
    """Rejeita milestone de manutenção em uma PR VEXT de desenvolvimento."""
    event = _event(
        base=DEVELOPMENT_BRANCH,
        milestone=MAINTENANCE_MILESTONE,
        title="feat(vext): build ecosystem artifacts",
    )
    errors = release.validate_pull_request(event)
    assert errors and DEVELOPMENT_MILESTONE in errors[0] and "VEXT" in errors[0]


def test_master_pr_requires_a_milestone() -> None:
    """Exige milestone explícita para PRs na base de desenvolvimento."""
    errors = release.validate_pull_request(
        _event(base=DEVELOPMENT_BRANCH, milestone=None)
    )
    assert errors and "milestone" in errors[0]


def test_maintenance_accepts_the_rolling_patch_milestone() -> None:
    """Aceita a milestone contínua da linha de manutenção."""
    assert (
        release.validate_pull_request(
            _event(base=MAINTENANCE_BRANCH, milestone=MAINTENANCE_MILESTONE)
        )
        == []
    )


def test_maintenance_rejects_exact_patch() -> None:
    """Rejeita uma milestone de patch exata na linha contínua."""
    errors = release.validate_pull_request(
        _event(base=MAINTENANCE_BRANCH, milestone="0.1.23")
    )
    assert errors and MAINTENANCE_MILESTONE in errors[0]


def test_maintenance_rejects_minor() -> None:
    """Rejeita a milestone de desenvolvimento na linha de manutenção."""
    errors = release.validate_pull_request(
        _event(base=MAINTENANCE_BRANCH, milestone=DEVELOPMENT_MILESTONE)
    )
    assert errors and MAINTENANCE_MILESTONE in errors[0]


def test_release_please_pr_is_exempt_with_controlled_source_and_label() -> None:
    """Isenta a branch versionada de desenvolvimento com origem confiável."""
    event = _event(
        base=MAINTENANCE_BRANCH,
        milestone=None,
        head=f"release-please-{DEVELOPMENT_MILESTONE}",
        labels=["autorelease: pending"],
    )
    assert release.validate_pull_request(event) == []


def test_release_branch_promotion_to_master_is_exempt() -> None:
    """Aceita uma promoção interna de qualquer branch com prefixo release/."""
    event = _event(
        base=MAINTENANCE_BRANCH,
        milestone=DEVELOPMENT_MILESTONE,
        head="release/0.3",
    )
    assert release.validate_pull_request(event) == []


def test_release_branch_promotion_from_another_repo_is_rejected() -> None:
    """Não permite que um fork use a exceção da promoção de release."""
    event = _event(
        base=MAINTENANCE_BRANCH,
        milestone=DEVELOPMENT_MILESTONE,
        head="release/0.3",
        head_repo="attacker/vectora",
    )
    errors = release.validate_pull_request(event)
    assert errors and MAINTENANCE_MILESTONE in errors[0]


def test_empty_release_branch_does_not_use_promotion_exception() -> None:
    """Exige a parte identificadora depois de ``release/``."""
    event = _event(
        base=MAINTENANCE_BRANCH,
        milestone=DEVELOPMENT_MILESTONE,
        head="release/",
    )
    errors = release.validate_pull_request(event)
    assert errors and MAINTENANCE_MILESTONE in errors[0]


def test_release_please_pr_from_another_repo_is_rejected() -> None:
    """Rejeita uma branch de release criada a partir de outro repositório."""
    event = _event(
        base=MAINTENANCE_BRANCH,
        milestone=None,
        head=f"release-please-{DEVELOPMENT_MILESTONE}",
        labels=["autorelease: pending"],
        head_repo="attacker/vectora",
    )
    errors = release.validate_pull_request(event)
    assert errors and "milestone" in errors[0]


def test_release_please_pr_without_pending_label_is_rejected() -> None:
    """Exige o label controlado para isentar uma PR automática de release."""
    event = _event(
        base=MAINTENANCE_BRANCH,
        milestone=None,
        head=f"release-please-{DEVELOPMENT_MILESTONE}",
    )
    errors = release.validate_pull_request(event)
    assert errors and "milestone" in errors[0]


def test_release_please_lookalike_branch_is_rejected() -> None:
    """Rejeita branches que apenas se parecem com o formato automático."""
    event = _event(
        base=MAINTENANCE_BRANCH,
        milestone=None,
        head=f"release-please-{DEVELOPMENT_MILESTONE}-lookalike",
        labels=["autorelease: pending"],
    )
    errors = release.validate_pull_request(event)
    assert errors and "milestone" in errors[0]


def test_release_please_minor_targeting_development_is_rejected() -> None:
    """Rejeita a minor quando ela ainda aponta para a branch de desenvolvimento."""
    event = _event(
        base=DEVELOPMENT_BRANCH,
        milestone=None,
        head=f"release-please-{DEVELOPMENT_MILESTONE}",
        labels=["autorelease: pending"],
    )
    errors = release.validate_pull_request(event)
    assert errors and "milestone" in errors[0]


def test_legacy_release_please_branch_retargeted_to_maintenance_is_rejected() -> None:
    """Rejeita o formato legado redirecionado à linha de manutenção."""
    event = _event(
        base=MAINTENANCE_BRANCH,
        milestone=None,
        head=f"release-please--branches--{DEVELOPMENT_BRANCH}--components--vectora",
        labels=["autorelease: pending"],
    )
    errors = release.validate_pull_request(event)
    assert errors and "milestone" in errors[0]


def test_release_please_patch_branch_is_exempt_on_maintenance_line() -> None:
    """Isenta uma branch de patch versionada na linha de manutenção."""
    event = _event(
        base=MAINTENANCE_BRANCH,
        milestone=None,
        head=f"release-please-{MAINTENANCE_MILESTONE.removesuffix('.x')}.1",
        labels=["autorelease: pending"],
    )
    assert release.validate_pull_request(event) == []


def test_unsupported_base_is_rejected() -> None:
    """Rejeita bases que não estão declaradas nas linhas ativas."""
    errors = release.validate_pull_request(
        _event(base="develop", milestone=DEVELOPMENT_MILESTONE)
    )
    assert errors and DEVELOPMENT_BRANCH in errors[0]


# ── normalize-changelog ──────────────────────────────────────────────────────


def _run(tmp_path: Path, content: str) -> tuple[str, bool, bool]:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(content, encoding="utf-8", newline="")
    changed = normalize_changelog(path)
    second_changed = normalize_changelog(path)
    return path.read_text(encoding="utf-8"), changed, second_changed


def test_normalizes_lists_and_adds_introductions(tmp_path: Path) -> None:
    result, changed, second_changed = _run(
        tmp_path,
        "## [1.0.0]\n\n### Features\n\n* new feature\n\n### Bug Fixes\n\n* fixed bug\n",
    )

    assert changed is True
    assert second_changed is False
    assert "Esta seção reúne as alterações publicadas nesta versão." in result
    assert "Os recursos incluídos nesta versão são listados abaixo." in result
    assert "As correções incluídas nesta versão são listadas abaixo." in result
    assert "\n- new feature\n" in result
    assert "\n- fixed bug\n" in result


def test_preserves_backtick_and_tilde_fenced_code(tmp_path: Path) -> None:
    result, changed, _ = _run(
        tmp_path,
        "## [1.0.0]\n\n### Features\n\n````python\n* inside backticks\n```\n* still inside\n````\n\n~~~~text\n* inside tildes\n~~~\n* still inside\n~~~~\n\n* outside\n",
    )

    assert changed is True
    assert "* inside backticks" in result
    assert "* still inside\n````" in result
    assert "* inside tildes" in result
    assert "* still inside\n~~~~" in result
    assert "\n- outside\n" in result


def test_preserves_headings_and_lists_inside_fenced_code(tmp_path: Path) -> None:
    result, changed, _ = _run(
        tmp_path,
        "## [1.0.0]\n\n### Features\n\n```text\n### Example\n\n* inside code\n```\n\n* outside\n",
    )

    assert changed is True
    assert "### Example\n\n* inside code\n```" in result
    assert "Esta seção reúne as alterações publicadas nesta versão." in result
    assert "\n- outside\n" in result


def test_preserves_indented_code_list_markers(tmp_path: Path) -> None:
    result, changed, _ = _run(
        tmp_path,
        "## [1.0.0]\n\n### Features\n\n    * indented code\n\t* tabbed code\n* outside\n",
    )

    assert changed is True
    assert "    * indented code\n\t* tabbed code\n- outside\n" in result


def test_ignores_mixed_fence_delimiters(tmp_path: Path) -> None:
    result, changed, _ = _run(
        tmp_path,
        "## [1.0.0]\n\n### Features\n\n~~~text\n* inside code\n~~~```\n* still inside\n~~~\n* outside\n",
    )

    assert changed is True
    assert "* inside code\n~~~```\n* still inside\n~~~" in result
    assert "\n- outside\n" in result


def test_allows_opposite_delimiter_at_start_of_fence_info(tmp_path: Path) -> None:
    result, changed, _ = _run(
        tmp_path,
        "## [1.0.0]\n\n### Features\n\n~~~`python\n* inside tilde fence\n~~~\n\n```~~~\n* inside backtick fence\n```\n\n* outside\n",
    )

    assert changed is True
    assert "* inside tilde fence" in result
    assert "* inside backtick fence" in result
    assert "\n- outside\n" in result


def test_does_not_treat_tab_indented_delimiter_as_fence(tmp_path: Path) -> None:
    result, changed, _ = _run(
        tmp_path,
        "## [1.0.0]\n\n### Features\n\n\t~~~\n* outside\n",
    )

    assert changed is True
    assert "\t~~~\n- outside\n" in result


def test_preserves_newline_style_and_unchanged_content(tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_bytes(b"## [1.0.0]\r\n\r\nTexto introdutorio.\r\n\r\n- pronto\r\n")
    assert normalize_changelog(path) is False
    assert normalize_changelog(path) is False
    assert path.read_bytes().endswith(b"\r\n")

"""CLI e runners para exportação e verificação de contratos do Vectora."""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404
import sys
import tempfile
from pathlib import Path
from typing import Any


def _setup_isolation_env() -> Path:
    temp_home = Path(tempfile.gettempdir()) / "vectora_contracts_isolation"
    temp_home.mkdir(parents=True, exist_ok=True)
    os.environ["VECTORA_HOME"] = str(temp_home)
    os.environ["VECTORA_AUTH_REQUIRED"] = "false"
    return temp_home


def _contracts_root() -> Path:
    # devtools/contracts -> devtools -> vectora -> backend / contracts
    return Path(__file__).resolve().parents[2] / "backend" / "contracts"


def export_http(contracts_dir: Path) -> None:
    """Exporta o schema OpenAPI e o inventário de rotas."""
    _setup_isolation_env()
    from backend.api.server import create_app
    from devtools.contracts.normalize import dumps_normalized
    from devtools.contracts.redaction import redact_payload

    app = create_app(serve_static=False)
    openapi_schema = app.openapi()
    sanitized_openapi = redact_payload(openapi_schema)

    http_dir = contracts_dir / "http"
    http_dir.mkdir(parents=True, exist_ok=True)

    (http_dir / "openapi.json").write_text(
        dumps_normalized(sanitized_openapi), encoding="utf-8"
    )

    routes_inventory: list[dict[str, Any]] = []
    routes_map: dict[str, set[str]] = {}
    for path, path_item in openapi_schema.get("paths", {}).items():
        methods = [
            m.upper()
            for m in path_item
            if m.lower()
            in {"get", "post", "put", "delete", "patch", "options", "head", "trace"}
        ]
        methods.sort()
        name = None
        for m in path_item.values():
            if isinstance(m, dict) and "operationId" in m:
                name = m["operationId"]
                break
        routes_inventory.append({"path": path, "methods": methods, "name": name})
        routes_map.setdefault(path, set()).update(methods)

    routes_inventory.sort(
        key=lambda r: (str(r["path"]), "".join(str(x) for x in r["methods"]))
    )
    (http_dir / "routes.json").write_text(
        dumps_normalized(routes_inventory), encoding="utf-8"
    )

    # Overlay curado de rotas críticas
    critical_routes: list[dict[str, Any]] = [
        {"path": "/health", "methods": ["GET"], "auth": False},
        {"path": "/health/live", "methods": ["GET"], "auth": False},
        {"path": "/health/ready", "methods": ["GET"], "auth": False},
        {"path": "/settings/flags", "methods": ["GET"], "auth": False},
        {"path": "/settings/prefs", "methods": ["GET", "PATCH"], "auth": True},
        {
            "path": "/vectora.chat.v1.ChatService/StreamChat",
            "methods": ["POST"],
            "auth": True,
        },
        {
            "path": "/vectora.chat.v1.ChatService/ResumeChat",
            "methods": ["POST"],
            "auth": True,
        },
    ]

    # Validação estrita: cada entrada de critical_routes deve existir em app.routes
    for cr in critical_routes:
        cr_path = str(cr["path"])
        if cr_path not in routes_map:
            raise ValueError(f"Rota crítica inexistente na aplicação: {cr_path}")
        cr_methods: list[str] = list(cr["methods"])  # type: ignore[arg-type]
        for m in cr_methods:
            if m not in routes_map[cr_path]:
                raise ValueError(f"Método {m} inexistente na rota crítica {cr_path}")

    (http_dir / "critical-routes.json").write_text(
        dumps_normalized(critical_routes), encoding="utf-8"
    )
    print("Exported HTTP contracts.")


def export_tools(contracts_dir: Path) -> None:
    """Exporta o catálogo canônico de ferramentas do agente."""
    from backend.nodes.tools import ALL_TOOL_SPECS
    from devtools.contracts.normalize import dumps_normalized
    from devtools.contracts.redaction import redact_payload

    tools_dir = contracts_dir / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)

    catalog: list[dict[str, Any]] = []
    for spec in ALL_TOOL_SPECS:
        schema = {}
        if spec.args_model is not None:
            schema = spec.args_model.model_json_schema()
        catalog.append(
            {
                "name": spec.name,
                "description": spec.description,
                "schema": redact_payload(schema),
                "destructive": getattr(spec.extras, "destructive", False),
                "category": getattr(spec.extras, "category", "general"),
                "render_hint": getattr(spec.extras, "render_hint", None),
            }
        )

    catalog.sort(key=lambda t: str(t["name"]))
    (tools_dir / "catalog.json").write_text(dumps_normalized(catalog), encoding="utf-8")
    print("Exported Tools catalog.")


def export_sse(contracts_dir: Path) -> None:
    """Exporta o schema dos eventos de streaming SSE."""
    from pydantic import TypeAdapter

    from backend.api.schemas import StreamChatEventPayload
    from devtools.contracts.normalize import dumps_normalized

    sse_dir = contracts_dir / "sse"
    sse_dir.mkdir(parents=True, exist_ok=True)

    adapter = TypeAdapter(StreamChatEventPayload)
    schema = adapter.json_schema()

    # Enriquecer cada definição referenciada com a propriedade "type" constante correspondente ao wire format
    from backend.api.schemas import _TYPE_MAP

    defs = schema.get("$defs", {})
    name_to_type = {cls.__name__: t for cls, t in _TYPE_MAP.items()}

    for ref_entry in schema.get("anyOf", []):
        ref = ref_entry.get("$ref", "")
        def_name = ref.split("/")[-1]
        if def_name in defs and def_name in name_to_type:
            event_def = defs[def_name]
            properties = event_def.setdefault("properties", {})
            properties["type"] = {
                "const": name_to_type[def_name],
                "title": "Type",
                "type": "string",
            }
            required = event_def.setdefault("required", [])
            if "type" not in required:
                required.append("type")
                required.sort()

    (sse_dir / "events.schema.json").write_text(
        dumps_normalized(schema), encoding="utf-8"
    )
    print("Exported SSE schema.")


def export_errors(contracts_dir: Path) -> None:
    """Exporta a matriz canônica de erros tipados."""
    from devtools.contracts.normalize import dumps_normalized

    errors_dir = contracts_dir / "errors"
    errors_dir.mkdir(parents=True, exist_ok=True)

    matrix = {
        "UNAUTHORIZED": {"status_code": 401, "retryable": False, "category": "auth"},
        "FORBIDDEN": {"status_code": 403, "retryable": False, "category": "auth"},
        "NOT_FOUND": {"status_code": 404, "retryable": False, "category": "client"},
        "RATE_LIMITED": {"status_code": 429, "retryable": True, "category": "client"},
        "BAD_REQUEST": {"status_code": 400, "retryable": False, "category": "client"},
        "INTERNAL": {"status_code": 500, "retryable": True, "category": "server"},
        "INTERNAL_ERROR": {"status_code": 500, "retryable": True, "category": "server"},
        "SERVICE_UNAVAILABLE": {
            "status_code": 503,
            "retryable": True,
            "category": "server",
        },
        "MODEL_NO_VISION": {
            "status_code": 400,
            "retryable": False,
            "category": "client",
        },
        "MODEL_NO_TOOL_CALLING": {
            "status_code": 400,
            "retryable": False,
            "category": "client",
        },
        "MODEL_NOT_ALLOWED": {
            "status_code": 403,
            "retryable": False,
            "category": "auth",
        },
        "EMPTY_MODEL_RESPONSE": {
            "status_code": 502,
            "retryable": True,
            "category": "server",
        },
    }
    (errors_dir / "matrix.json").write_text(dumps_normalized(matrix), encoding="utf-8")
    print("Exported Errors matrix.")


def export_settings(contracts_dir: Path) -> None:
    """Exporta o schema canônico do settings.json (v1) e definições de perfil."""
    from backend.contracts.models import UserSettingsV1
    from devtools.contracts.normalize import dumps_normalized

    settings_dir = contracts_dir / "settings"
    settings_dir.mkdir(parents=True, exist_ok=True)

    schema = UserSettingsV1.model_json_schema()
    (settings_dir / "v1.schema.json").write_text(
        dumps_normalized(schema), encoding="utf-8"
    )
    print("Exported Settings schema.")


def export_quality_gates(contracts_dir: Path) -> None:
    """Exporta os schemas de Quality Gates e perfis de verificação pós-execução."""
    from backend.contracts.models import (
        QualityGateRunResult,
        QualityGateSpec,
        WorkspaceVerificationProfile,
    )
    from devtools.contracts.normalize import dumps_normalized

    gates_dir = contracts_dir / "quality_gates"
    gates_dir.mkdir(parents=True, exist_ok=True)

    profile_schema = WorkspaceVerificationProfile.model_json_schema()
    spec_schema = QualityGateSpec.model_json_schema()
    result_schema = QualityGateRunResult.model_json_schema()

    bundle = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "QualityGatesVerificationBundle",
        "description": "Schemas de perfis e resultados de verificação pós-execução.",
        "definitions": {
            "WorkspaceVerificationProfile": profile_schema,
            "QualityGateSpec": spec_schema,
            "QualityGateRunResult": result_schema,
        },
    }
    (gates_dir / "schema.json").write_text(dumps_normalized(bundle), encoding="utf-8")
    print("Exported Quality Gates schema.")


def export_typescript(contracts_dir: Path) -> None:
    """Exporta definições de tipos TypeScript sincronizadas com os contratos canônicos."""
    frontend_contracts_dir = (
        Path(__file__).resolve().parents[2] / "frontend" / "lib" / "contracts"
    )
    frontend_contracts_dir.mkdir(parents=True, exist_ok=True)

    ts_content = """/**
 * CANONICAL TYPESCRIPT CONTRACTS (AUTO-GENERATED)
 *
 * Fonte da verdade: vectora/backend/contracts/
 * Atualizado via: uv run python -m devtools.contracts export
 * NÃO EDITAR MANUALMENTE.
 */

// ============================================================================
// Errors Matrix (vectora/backend/contracts/errors/matrix.json)
// ============================================================================

export type CanonicalErrorCode =
  | "UNAUTHORIZED"
  | "FORBIDDEN"
  | "NOT_FOUND"
  | "RATE_LIMITED"
  | "BAD_REQUEST"
  | "INTERNAL"
  | "INTERNAL_ERROR"
  | "SERVICE_UNAVAILABLE"
  | "MODEL_NO_VISION"
  | "MODEL_NO_TOOL_CALLING"
  | "MODEL_NOT_ALLOWED"
  | "EMPTY_MODEL_RESPONSE";

export interface CanonicalErrorSpec {
  status_code: number;
  retryable: boolean;
  category: "auth" | "client" | "server";
}

// ============================================================================
// Settings Schema v1 (vectora/backend/contracts/settings/v1.schema.json)
// ============================================================================

export interface LocaleSettings {
  language: string;
  timezone: string;
}

export interface AppearanceSettings {
  theme: "dark" | "light" | "system";
  sidebarPosition: "left" | "right";
}

export interface SuggestionsSettings {
  enabled: boolean;
  aiEnabled: boolean;
}

export interface LintSettings {
  enabled: boolean;
  showDiagnostics: boolean;
}

export interface EditorSettings {
  fontFamily: string;
  fontSize: number;
  lineHeight: number;
  fontLigatures: boolean;
  wordWrap: "off" | "on" | "wordWrapColumn" | "bounded";
  minimap: boolean;
  smoothScrolling: boolean;
  formatOnSave: boolean;
  formatOnPaste: boolean;
  suggestions: SuggestionsSettings;
  lint: LintSettings;
}

export interface WatcherSettings {
  enabled: boolean;
  exclude: string[];
}

export interface FileSettings {
  autoSave: "off" | "afterDelay" | "onFocusChange" | "onWindowChange";
  autoSaveDelayMs: number;
  encoding: string;
  eol: "lf" | "crlf" | "auto";
  trimTrailingWhitespace: boolean;
  insertFinalNewline: boolean;
  watcher: WatcherSettings;
}

export interface WorkbenchesSettings {
  git: Record<string, unknown>;
  plan: Record<string, unknown>;
  tasks: Record<string, unknown>;
  browser: Record<string, unknown>;
  storage: Record<string, unknown>;
  contextGraph: Record<string, unknown>;
  library: Record<string, unknown>;
  terminal: Record<string, unknown>;
}

export interface UpdatesSettings {
  autoUpdateEnabled: boolean;
}

export interface UserSettingsV1 {
  $schema?: string;
  version: number;
  locale: LocaleSettings;
  appearance: AppearanceSettings;
  editor: EditorSettings;
  files: FileSettings;
  workbenches: WorkbenchesSettings;
  updates: UpdatesSettings;
}

// ============================================================================
// Quality Gates & Post-Run Verification (vectora/backend/contracts/quality_gates/schema.json)
// ============================================================================

export type QualityGatePolicy = "block" | "degrade" | "inform";

export interface QualityGateSpec {
  id: string;
  name: string;
  command: string[];
  policy: QualityGatePolicy;
  file_patterns?: string[];
  timeout_seconds?: number;
}

export interface WorkspaceVerificationProfile {
  workspace_id: string;
  enabled: boolean;
  gates: QualityGateSpec[];
  require_clean_git?: boolean;
  inject_stack_trace_on_failure?: boolean;
}

export interface QualityGateRunResult {
  gate_id: string;
  passed: boolean;
  exit_code: number;
  output?: string;
  duration_ms?: number;
  artifacts_collected?: string[];
}
"""
    (frontend_contracts_dir / "index.ts").write_text(ts_content, encoding="utf-8")
    print("Exported TypeScript contracts.")


def export_all(contracts_dir: Path, only: str | None = None) -> None:
    if only in (None, "http"):
        export_http(contracts_dir)
    if only in (None, "tools"):
        export_tools(contracts_dir)
    if only in (None, "sse"):
        export_sse(contracts_dir)
    if only in (None, "errors"):
        export_errors(contracts_dir)
    if only in (None, "settings"):
        export_settings(contracts_dir)
    if only in (None, "quality_gates"):
        export_quality_gates(contracts_dir)
    if only in (None, "typescript"):
        export_typescript(contracts_dir)


def check_all(contracts_dir: Path) -> int:
    """Verifica se há drift entre os contratos exportados e a implementação."""
    from devtools.contracts.compat import check_schema_compatibility

    _setup_isolation_env()

    with tempfile.TemporaryDirectory() as tmp_str:
        temp_dir = Path(tmp_str)
        export_all(temp_dir)

        has_diff = False

        # 1. Checa se algum arquivo gerado está ausente ou tem drift em relação a contracts_dir
        for expected_file in temp_dir.rglob("*.json"):
            rel = expected_file.relative_to(temp_dir)
            target = contracts_dir / rel
            if not target.exists():
                print(f"MISSING: {rel}")
                has_diff = True
                continue
            expected_data = json.loads(expected_file.read_text(encoding="utf-8"))
            current_data = json.loads(target.read_text(encoding="utf-8"))
            if expected_data != current_data:
                print(f"DRIFT DETECTED: {rel}")
                has_diff = True

            # Se for schema OpenAPI ou SSE, executa check_schema_compatibility
            # Carrega baseline do branch base se disponível (ex: git show origin/release/0.3:...),
            # ou usa current_data (artefato em disco/PR) como baseline local.
            baseline_data = current_data
            try:
                git_rel = f"vectora/{contracts_dir.name}/{rel.as_posix()}"
                res = subprocess.run(  # nosec B603, B607 # noqa: S603
                    ["git", "show", f"origin/release/0.3:{git_rel}"],  # noqa: S607
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if res.returncode == 0 and res.stdout.strip():
                    baseline_data = json.loads(res.stdout)
            except Exception:
                baseline_data = current_data

            if (
                rel.name == "openapi.json"
                and isinstance(baseline_data, dict)
                and isinstance(expected_data, dict)
            ):
                # Comparar cada schema de components.schemas individualmente
                old_schemas = baseline_data.get("components", {}).get("schemas", {})
                new_schemas = expected_data.get("components", {}).get("schemas", {})
                for s_name, old_s in old_schemas.items():
                    if s_name in new_schemas:
                        new_s = new_schemas[s_name]
                        # Decide direção: request schemas terminam em Request / Config / Input / etc.
                        direction = (
                            "request"
                            if any(
                                k in s_name.lower()
                                for k in ("request", "config", "input", "query")
                            )
                            else "response"
                        )
                        report = check_schema_compatibility(
                            old_s,
                            new_s,
                            path=f"components.schemas.{s_name}",
                            direction=direction,
                        )
                        if not report.is_compatible:
                            print(
                                f"BREAKING SCHEMA CHANGE in openapi.json ({s_name}): {report.breaking}"
                            )
                            has_diff = True

            elif (
                rel.name == "events.schema.json"
                and isinstance(baseline_data, dict)
                and isinstance(expected_data, dict)
            ):
                # SSE events são eventos de resposta / streaming
                old_defs = baseline_data.get("$defs", {})
                new_defs = expected_data.get("$defs", {})
                for d_name, old_d in old_defs.items():
                    if d_name in new_defs:
                        new_d = new_defs[d_name]
                        report = check_schema_compatibility(
                            old_d, new_d, path=f"$defs.{d_name}", direction="response"
                        )
                        if not report.is_compatible:
                            print(
                                f"BREAKING SSE SCHEMA CHANGE ({d_name}): {report.breaking}"
                            )
                            has_diff = True

        # 2. Checa se há arquivos órfãos / obsoletos em contracts_dir que não foram gerados
        for target_file in contracts_dir.rglob("*.json"):
            rel = target_file.relative_to(contracts_dir)
            expected = temp_dir / rel
            if not expected.exists():
                print(f"OBSOLETE FILE DETECTED: {rel}")
                has_diff = True

        if has_diff:
            print("Contracts check failed: drift detected.")
            return 1

    print("All contracts match. No drift detected.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Vectora Contracts CLI")
    subparsers = parser.add_subparsers(dest="command")

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument(
        "--only",
        choices=[
            "http",
            "tools",
            "sse",
            "errors",
            "settings",
            "quality_gates",
            "typescript",
        ],
        help="Exporta apenas uma área",
    )

    subparsers.add_parser("check")

    args = parser.parse_args()
    contracts_dir = _contracts_root()

    if args.command == "export":
        export_all(contracts_dir, only=args.only)
    elif args.command == "check":
        sys.exit(check_all(contracts_dir))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

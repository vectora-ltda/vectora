"""CLI e runners para exportação e verificação de contratos do Vectora."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


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

    routes_inventory = []
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

    routes_inventory.sort(key=lambda r: (r["path"], "".join(r["methods"])))
    (http_dir / "routes.json").write_text(
        dumps_normalized(routes_inventory), encoding="utf-8"
    )

    # Overlay curado de rotas críticas
    critical_routes = [
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
        cr_path = cr["path"]
        if cr_path not in routes_map:
            raise ValueError(f"Rota crítica inexistente na aplicação: {cr_path}")
        for m in cr["methods"]:
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

    catalog = []
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

    catalog.sort(key=lambda t: t["name"])
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


def export_all(contracts_dir: Path, only: str | None = None) -> None:
    if only in (None, "http"):
        export_http(contracts_dir)
    if only in (None, "tools"):
        export_tools(contracts_dir)
    if only in (None, "sse"):
        export_sse(contracts_dir)
    if only in (None, "errors"):
        export_errors(contracts_dir)


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
            if (
                rel.name == "openapi.json"
                and isinstance(current_data, dict)
                and isinstance(expected_data, dict)
            ):
                report = check_schema_compatibility(current_data, expected_data)
                if not report.is_compatible:
                    print(f"BREAKING SCHEMA CHANGE in {rel}: {report.breaking}")
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
        choices=["http", "tools", "sse", "errors"],
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

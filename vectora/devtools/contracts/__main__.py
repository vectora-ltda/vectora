"""CLI e runners para exportação e verificação de contratos do Vectora."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# Configuração de isolamento do ambiente antes de importar qualquer módulo do backend
_TEMP_HOME = Path(tempfile.gettempdir()) / "vectora_contracts_isolation"
_TEMP_HOME.mkdir(parents=True, exist_ok=True)
os.environ["VECTORA_HOME"] = str(_TEMP_HOME)
os.environ["VECTORA_AUTH_REQUIRED"] = "false"


def _contracts_root() -> Path:
    # devtools/contracts -> devtools -> vectora -> backend / contracts
    return Path(__file__).resolve().parents[2] / "backend" / "contracts"


def export_http(contracts_dir: Path) -> None:
    """Exporta o schema OpenAPI e o inventário de rotas."""
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
    for route in app.routes:
        methods = sorted(getattr(route, "methods", []))
        path = getattr(route, "path", None)
        name = getattr(route, "name", None)
        if path:
            routes_inventory.append({"path": path, "methods": methods, "name": name})

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
        {"path": "/settings/prefs", "methods": ["GET", "POST"], "auth": True},
        {"path": "/chat", "methods": ["POST"], "auth": True},
        {"path": "/threads", "methods": ["GET", "POST"], "auth": True},
    ]
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
        "INTERNAL_ERROR": {"status_code": 500, "retryable": True, "category": "server"},
        "SERVICE_UNAVAILABLE": {
            "status_code": 503,
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
    # Para o check, exportamos temporariamente e comparamos
    temp_dir = _TEMP_HOME / "check_contracts"
    temp_dir.mkdir(parents=True, exist_ok=True)
    export_all(temp_dir)

    has_diff = False
    for expected_file in temp_dir.rglob("*.json"):
        rel = expected_file.relative_to(temp_dir)
        target = contracts_dir / rel
        if not target.exists():
            print(f"MISSING: {rel}")
            has_diff = True
            continue
        expected_content = expected_file.read_text(encoding="utf-8")
        current_content = target.read_text(encoding="utf-8")
        if expected_content != current_content:
            print(f"DRIFT DETECTED: {rel}")
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

"""Trilha de auditoria do sandbox: o que foi decidido, por quem e com qual política.

Cada registro leva o hash do anterior (`prev`) e o seu próprio (`hash`, SHA-256
do JSON canônico do registro sem o campo `hash`): alterar, remover ou reordenar
uma linha quebra a cadeia, e `verify()` aponta onde. Valores secretos nunca
entram: chaves sensíveis são mascaradas e textos longos, cortados.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

AUDIT_FILE_ENV = "VECTORA_SANDBOX_AUDIT_FILE"
#: Acima deste tamanho o arquivo é arquivado em `.1` e a cadeia continua.
MAX_FILE_BYTES = 5 * 1024 * 1024
_GENESIS = "0" * 64
_MAX_TEXT = 500
_SENSITIVE = ("token", "key", "secret", "password", "passwd", "credential", "auth")

#: Eventos conhecidos; qualquer outro nome é recusado, para a trilha não virar
#: um depósito livre.
EVENTS = frozenset(
    {
        "worker_started",
        "worker_refused",
        "egress",
        "policy_changed",
    }
)


def audit_file_path() -> Path:
    override = os.environ.get(AUDIT_FILE_ENV)
    if override:
        return Path(override)
    from backend.settings import settings

    return Path(settings.vectora_home) / "sandbox-audit.jsonl"


def redact(value: Any, key: str = "") -> Any:
    """Cópia de `value` sem segredos: chaves sensíveis viram `***` e textos
    longos são cortados."""
    if any(part in key.lower() for part in _SENSITIVE):
        return "***"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, key) for item in value]
    if isinstance(value, str) and len(value) > _MAX_TEXT:
        return value[:_MAX_TEXT] + "…"
    return value


def _digest(record: dict[str, Any]) -> str:
    body = {name: record[name] for name in record if name != "hash"}
    canonical = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    records: int
    #: Linha (começando em 1) da primeira quebra; `None` quando a cadeia fecha.
    broken_at: int | None = None
    reason: str = ""


class SandboxAuditLog:
    """Arquivo JSON Lines encadeado por hash."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._tail: tuple[int, str] | None = None

    @property
    def path(self) -> Path:
        return self._path or audit_file_path()

    def append(
        self, workspace_id: str, event: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if event not in EVENTS:
            raise ValueError(f"evento de auditoria desconhecido: {event!r}")
        with self._lock:
            path = self.path
            path.parent.mkdir(parents=True, exist_ok=True)
            seq, previous = self._tail_of(path)
            record: dict[str, Any] = {
                "seq": seq + 1,
                "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "workspace_id": workspace_id,
                "event": event,
                "data": redact(data or {}),
                "prev": previous,
            }
            record["hash"] = _digest(record)
            line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line)
            self._tail = (record["seq"], record["hash"])
            if path.stat().st_size > MAX_FILE_BYTES:
                path.replace(path.with_suffix(path.suffix + ".1"))
            return record

    def _tail_of(self, path: Path) -> tuple[int, str]:
        if self._tail is not None and path.exists():
            return self._tail
        self._tail = None
        last = _last_record(path) or _last_record(path.with_suffix(path.suffix + ".1"))
        self._tail = (int(last["seq"]), str(last["hash"])) if last else (0, _GENESIS)
        return self._tail

    def read(
        self, *, workspace_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Os registros mais recentes (do mais novo ao mais antigo)."""
        records = _read_all(self.path)
        if workspace_id is not None:
            records = [r for r in records if r.get("workspace_id") == workspace_id]
        return list(reversed(records[-max(1, limit) :]))

    def verify(self) -> VerifyResult:
        """Confere a cadeia do arquivo atual de ponta a ponta."""
        path = self.path
        if not path.exists():
            return VerifyResult(ok=True, records=0)
        previous: str | None = None
        expected_seq: int | None = None
        count = 0
        with path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    return VerifyResult(False, count, number, "linha ilegível")
                if record.get("hash") != _digest(record):
                    return VerifyResult(False, count, number, "hash não confere")
                if previous is not None and record.get("prev") != previous:
                    return VerifyResult(False, count, number, "cadeia interrompida")
                if expected_seq is not None and record.get("seq") != expected_seq:
                    return VerifyResult(False, count, number, "sequência interrompida")
                previous = record["hash"]
                expected_seq = int(record["seq"]) + 1
                count += 1
        return VerifyResult(True, count)


def _last_record(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    last: str | None = None
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                last = line
    if last is None:
        return None
    try:
        record = json.loads(last)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def _read_all(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    return records


audit_log = SandboxAuditLog()


def record(workspace_id: str, event: str, data: dict[str, Any] | None = None) -> None:
    """Registra sem nunca derrubar quem chamou: uma falha de disco na auditoria
    não pode impedir o jail de funcionar (o erro vai ao log)."""
    import logging

    try:
        audit_log.append(workspace_id, event, data)
    except Exception:
        logging.getLogger(__name__).exception("sandbox: falha ao gravar a auditoria")

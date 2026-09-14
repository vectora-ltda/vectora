"""Isolated runtime host for built VEXT artifacts.

The host never imports extension code into the Vectora process.  It verifies
the artifact, extracts it into a private temporary directory, and starts a
runtime adapter selected by the manifest.  Adapters communicate through a
small newline-delimited JSON-RPC contract.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import shutil as shutil_module
import subprocess  # nosec B404 - process is selected from validated runtime metadata
import sys
import tempfile
import threading
import zipfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Self

from backend.services.vext import VextManifest, ensure_supported_platform
from backend.services.vext_artifact import verify_vext
from backend.services.vext_registry import VextTrustStore

MAX_RPC_LINE: Final = 1 * 1024 * 1024
OVERSIZED_RESPONSE: Final = "__vectora_vext_oversized_response__"
DEFAULT_REQUEST_TIMEOUT: Final = 10.0
MAX_STDERR_BYTES: Final = 256 * 1024


@dataclass(frozen=True, slots=True)
class CapabilityPolicy:
    """Capabilities granted to one extension host instance."""

    allowed: frozenset[str]

    @classmethod
    def from_manifest(cls, manifest: VextManifest) -> CapabilityPolicy:
        return cls(frozenset(manifest.permissions))

    def require(self, capability: str) -> None:
        if capability not in self.allowed:
            raise PermissionError(f"capability não concedida: {capability}")


class VextHost:
    """Manage one isolated Node or Python extension process."""

    def __init__(
        self,
        artifact: str | Path,
        *,
        python_executable: str | None = None,
        trust_store: VextTrustStore | None = None,
        allow_unsigned: bool = False,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        sandbox_required: bool = True,
    ) -> None:
        self.artifact = Path(artifact)
        self.python_executable = python_executable or sys.executable
        self.process: subprocess.Popen[str] | None = None
        self.root: Path | None = None
        self.manifest: VextManifest | None = None
        self.policy: CapabilityPolicy | None = None
        self.trust_store = trust_store
        self.allow_unsigned = allow_unsigned
        self.request_timeout = request_timeout
        self.sandbox_required = sandbox_required
        self._response_queue: queue.Queue[str] = queue.Queue()
        self._request_lock = threading.Lock()
        self._reader_threads: list[threading.Thread] = []
        self._stderr_buffer: deque[str] = deque(maxlen=1)
        self._next_request_id = 0

    def start(self) -> VextManifest:
        """Verify and start the runtime adapter for the artifact."""
        result = verify_vext(self.artifact)
        ensure_supported_platform(result.manifest)
        if self.trust_store is not None:
            self.trust_store.verify(self.artifact)
        elif not self.allow_unsigned:
            raise PermissionError("artefato VEXT exige publisher confiável")
        self.manifest = result.manifest
        self.policy = CapabilityPolicy.from_manifest(result.manifest)
        self.root = Path(tempfile.mkdtemp(prefix="vectora-vext-"))
        try:
            with zipfile.ZipFile(self.artifact) as archive:
                root = self.root.resolve()
                for info in archive.infolist():
                    target = (root / info.filename).resolve()
                    if target != root and root not in target.parents:
                        raise ValueError("membro do pacote fora do diretório isolado")
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open("wb") as destination:
                        shutil.copyfileobj(source, destination)
            entrypoint = (
                result.manifest.backend_entrypoint or result.manifest.entrypoint
            )
            entry = (self.root / entrypoint).resolve()
            if self.root.resolve() not in entry.parents:
                raise ValueError("entrypoint fora do diretório isolado")
            runtime = result.manifest.runtime
            if runtime == "python":
                command = [
                    self.python_executable,
                    str(Path(__file__).with_name("vext_runtime.py")),
                    "--root",
                    str(self.root),
                    "--entrypoint",
                    entrypoint,
                ]
            elif runtime == "node":
                node_executable = shutil.which("node")
                if node_executable is None:
                    raise RuntimeError("runtime Node.js não está disponível")
                command = [
                    node_executable,
                    str(Path(__file__).with_name("vext_node_runtime.mjs")),
                    "--root",
                    str(self.root),
                    "--entrypoint",
                    entrypoint,
                ]
            elif runtime == "none":
                self.stop()
                return result.manifest
            else:
                raise ValueError(f"runtime não suportado: {runtime}")
            command = self._sandbox_command(
                command,
                self.root,
                result.manifest,
                required=self.sandbox_required,
            )
            environment = {
                "PATH": os.defpath,
                "VECTORA_VEXT_ID": result.manifest.id,
                "VECTORA_VEXT_ROOT": str(self.root),
                "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            }
            self.process = subprocess.Popen(  # noqa: S603  # nosec B603 - validated runtime command
                command,
                cwd=self.root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                shell=False,
            )
            self._start_pipe_readers()
            return result.manifest
        except Exception:
            self.stop()
            raise

    @staticmethod
    def _sandbox_command(
        command: list[str],
        root: Path,
        manifest: VextManifest,
        *,
        required: bool = True,
    ) -> list[str]:
        """Wrap runtimes in bubblewrap when available on Linux.

        The wrapper removes network access by default and exposes only the
        extracted extension as writable state. Hosts that require OS sandboxing
        can set ``VECTORA_VEXT_REQUIRE_SANDBOX=1``; unsupported platforms then
        fail closed instead of presenting manifest permissions as isolation.
        """
        required = required or os.environ.get("VECTORA_VEXT_REQUIRE_SANDBOX") == "1"
        bubblewrap = shutil_module.which("bwrap")
        if bubblewrap is None:
            if required:
                raise RuntimeError("sandbox VEXT indisponível neste sistema")
            return command
        wrapped = [
            bubblewrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup",
            "--cap-drop",
            "ALL",
            "--proc",
            "/proc",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/bin",
            "/bin",
            "--ro-bind",
            "/lib",
            "/lib",
            "--ro-bind",
            "/etc",
            "/etc",
            "--bind",
            str(root),
            str(root),
            "--chdir",
            str(root),
        ]
        if Path("/lib64").exists():
            wrapped.extend(["--ro-bind", "/lib64", "/lib64"])
        for system_path in (Path("/app"), Path("/opt")):
            if system_path.exists():
                wrapped.extend(["--ro-bind", str(system_path), str(system_path)])
        if "network" not in manifest.permissions:
            wrapped.append("--unshare-net")
        return [*wrapped, "--", *command]

    def _start_pipe_readers(self) -> None:
        """Drain child output continuously so a noisy extension cannot deadlock."""
        process = self.process
        if process is None or process.stdout is None or process.stderr is None:
            raise RuntimeError("pipes do runtime VEXT indisponíveis")
        stdout = process.stdout
        stderr = process.stderr

        def read_stdout() -> None:
            while True:
                line = stdout.readline(MAX_RPC_LINE + 1)
                if not line:
                    return
                if len(line.encode("utf-8")) > MAX_RPC_LINE:
                    while not line.endswith("\n"):
                        line = stdout.readline(MAX_RPC_LINE + 1)
                        if not line:
                            break
                    self._response_queue.put(OVERSIZED_RESPONSE)
                    continue
                self._response_queue.put(line)

        def append_stderr(chunk: str) -> None:
            current = self._stderr_buffer[0] if self._stderr_buffer else ""
            data = (current + chunk).encode("utf-8")
            if len(data) > MAX_STDERR_BYTES:
                data = data[-MAX_STDERR_BYTES:]
            self._stderr_buffer.clear()
            self._stderr_buffer.append(data.decode("utf-8", errors="replace"))

        def read_stderr() -> None:
            while True:
                chunk = stderr.read(4096)
                if not chunk:
                    return
                append_stderr(chunk)

        for target in (read_stdout, read_stderr):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self._reader_threads.append(thread)

    def request(
        self,
        method: str,
        params: dict[str, object] | None = None,
        *,
        capability: str | None = None,
    ) -> dict[str, object]:
        """Send one JSON-RPC request after enforcing its capability."""
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("host VEXT não iniciado")
        if capability is not None:
            if self.policy is None:
                raise RuntimeError("política de capabilities indisponível")
            self.policy.require(capability)
        self._next_request_id += 1
        request_id = self._next_request_id
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_RPC_LINE:
            raise ValueError("mensagem JSON-RPC excede o limite")
        with self._request_lock:
            try:
                self.process.stdin.write(encoded + "\n")
                self.process.stdin.flush()
                line = self._response_queue.get(timeout=self.request_timeout)
            except queue.Empty as exc:
                self.stop()
                raise TimeoutError("runtime VEXT excedeu o prazo de resposta") from exc
            except (BrokenPipeError, OSError) as exc:
                self.stop()
                raise RuntimeError(
                    "não foi possível enviar solicitação ao runtime VEXT"
                ) from exc
        if line == OVERSIZED_RESPONSE or len(line.encode("utf-8")) > MAX_RPC_LINE:
            self.stop()
            raise ValueError("resposta JSON-RPC excede o limite")
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            self.stop()
            raise ValueError("resposta JSON-RPC inválida") from exc
        if not isinstance(response, dict):
            self.stop()
            raise ValueError("resposta JSON-RPC inválida")
        if response.get("jsonrpc") != "2.0" or response.get("id") != request_id:
            self.stop()
            raise ValueError("resposta JSON-RPC não corresponde à solicitação")
        if ("result" in response) == ("error" in response):
            self.stop()
            raise ValueError("resposta JSON-RPC inválida")
        return response

    def stop(self) -> None:
        """Terminate the adapter and delete its extracted artifact."""
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.process = None
        self._reader_threads.clear()
        self._response_queue = queue.Queue()
        if self.root is not None:
            shutil.rmtree(self.root, ignore_errors=True)
        self.root = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


__all__ = ["CapabilityPolicy", "VextHost"]

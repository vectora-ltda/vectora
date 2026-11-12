"""
Vectora — SConstruct (SCons build file)

Uso (PowerShell / cmd, a partir da raiz do repositório):
    scons               → exibe ajuda
    scons release       → build completo + instalador para o SO atual
    scons smoke         → builda o binário híbrido e confirma que /health sobe de verdade
    scons tests         → frontend + backend (sem cobertura)
    scons coverage      → scons tests com relatório de cobertura
    scons lint          → ruff+ty+bandit (backend) + tsc+oxlint (frontend)
    scons update        → atualiza deps: uv (backend) + pnpm (frontend), respeitando ranges/lockfiles
    scons update --latest → mesma coisa, mas ignora ranges/lockfiles (pnpm
                          --latest cruza majors) — usar só antes de uma migração
                          grande, seguido de revisão manual de cada breaking change
    scons docker        → sobe PostgreSQL + Redis + Qdrant via docker compose
    scons clean         → remove outputs de build

Pré-requisitos: uv e pnpm (o PyInstaller vem no uv.lock)
O runtime de produção e os testes E2E usam o Chromium distribuído pelo pacote
Electron. Playwright é somente o cliente CDP; nenhum browser Playwright
separado é instalado ou empacotado.
Instalar SCons: pip install scons (ou uv add --dev scons)

Cobertura de lint e tests:
    backend/        Python (ruff, ty, bandit) + pytest
    frontend/       TypeScript (tsc, oxlint, vitest) — inclui frontend/electron/,
                    fundido no package.json do frontend (só o tsconfig de
                    compilação do Electron é separado)
"""

import base64
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import TextIO

from utils import build_env
from utils.subprocess_compat import command_for_execution

AddOption(
    "--latest",
    dest="latest",
    action="store_true",
    default=False,
    help="scons update --latest: ignora ranges/lockfiles, força major mais novo",
)

# ── Helpers ───────────────────────────────────────────────────────────────────

ROOT = Dir(".").abspath

VECTORA  = ROOT

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _find_pnpm() -> str:
    if sys.platform == "win32":
        for name in ("pnpm.cmd", "pnpm.exe", "pnpm"):
            found = shutil.which(name)
            if found:
                return found
    return shutil.which("pnpm") or "pnpm"


PNPM = _find_pnpm()


def _find_bin(name: str) -> str:
    if sys.platform == "win32":
        for candidate in (f"{name}.cmd", f"{name}.exe", name):
            found = shutil.which(candidate)
            if found:
                return found
    return shutil.which(name) or name


def _echo(line: str) -> None:
    """Ecoa uma linha no stdout sem quebrar em consoles que não codificam o
    caractere (cp1252 no Windows): o que não cabe vira `?`. O log em arquivo
    continua em UTF-8 e guarda a linha original."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    sys.stdout.write(line.encode(encoding, errors="replace").decode(encoding))


def _run(
    cmd: list[str],
    env: dict | None = None,
    cwd: str | None = None,
    log=None,
    discard_stderr: bool = False,
) -> None:
    """Executa um comando, falha se o código de retorno for ≠ 0.

    Com ``log`` (file handle), espelha stdout+stderr para o terminal E para o
    arquivo (tee). O pipe desliga cores ANSI, deixando o .txt limpo.

    ``discard_stderr=True`` roteia stderr para DEVNULL — útil para ferramentas
    que emitem avisos ruidosos em stderr mas reportam erros reais via código de
    retorno (ex: bandit com comentários em língua não-inglesa).

    stdin sempre vai para DEVNULL: sem isso, o subprocesso herda o stdin do
    scons e qualquer prompt interativo não coberto pelas flags de automação
    (--yes/--frozen-lockfile/etc — ex.: já observado no `wrangler d1
    migrations apply` e no `vercel --prod` do deploy de produção) trava pra
    sempre esperando uma resposta que nunca chega, em silêncio (a própria
    saída do prompt fica bufferizada e nunca aparece no log). Com stdin
    fechado, qualquer prompt recebe EOF na hora e o comando falha rápido e
    visível em vez de travar por horas.
    """
    merged = {**os.environ, **(env or {})}
    run_cwd = cwd or ROOT
    cmd = command_for_execution(cmd)
    header = f"\n>> [{os.path.relpath(run_cwd, ROOT)}] {' '.join(str(c) for c in cmd)}"
    print(header)
    stderr_dest = subprocess.DEVNULL if discard_stderr else None
    if log is None:
        result = subprocess.run(
            cmd, cwd=run_cwd, env=merged, stderr=stderr_dest,
            stdin=subprocess.DEVNULL,
        )
        rc = result.returncode
    else:
        merged["PYTHONIOENCODING"] = "utf-8"
        log.write(header + "\n")
        log.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=run_cwd,
            env=merged,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL if discard_stderr else subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        for line in proc.stdout:  # type: ignore[union-attr]
            _echo(line)
            log.write(_ANSI_RE.sub("", line))
        proc.wait()
        rc = proc.returncode
    if rc != 0:
        raise SystemExit(rc)


#: `_run` fecha o stdin, então o pnpm não pode perguntar antes de recriar um
#: node_modules incompatível (troca de store/versão do pnpm). Sem
#: `confirmModulesPurge=false` ele aborta com
#: ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY em vez de reinstalar.
PNPM_INSTALL = ["install", "--frozen-lockfile", "--config.confirmModulesPurge=false"]
PNPM_CI = ["ci", "--config.confirmModulesPurge=false"]


def _open_log(name: str):
    logdir = os.path.join(ROOT, ".scons-logs")
    os.makedirs(logdir, exist_ok=True)
    return open(os.path.join(logdir, f"{name}.txt"), "w", encoding="utf-8")


# ── Ações de build ────────────────────────────────────────────────────────────


def _action_build_chat(target, source, env):
    frontend_dir = os.path.join(VECTORA, "frontend")
    if not os.path.isdir(os.path.join(frontend_dir, "node_modules")):
        raise SystemExit(
            "Dependências do frontend ausentes. Rode `scons install` uma vez "
            "para preparar o ambiente; `scons frontend` apenas compila."
        )
    node_env = {"NODE_NO_WARNINGS": "1"}
    _run([PNPM, "build"], env=node_env, cwd=frontend_dir)

    dist = os.path.join(VECTORA, "frontend", "dist")
    if not os.path.isdir(dist) or not os.path.isfile(os.path.join(dist, "index.html")):
        raise SystemExit(
            "ERRO: frontend/dist/index.html não foi gerado. "
            "Verifique a configuração do Vite em frontend/vite.config.mts."
        )
    print(f">> chat dist pronto em {dist}")


def _action_build_backend(target, source, env):
    # Empacota o backend: o PyInstaller coleta o pacote `backend` (código e
    # dados, inclusive o defaults.env com o app secret do release), as libs
    # Python e os sidecars numa pasta dist/vectora/ (--onedir). O backend não
    # é mais compilado em C: não há toolchain C (MSVC, clang, gcc) no build.
    _run([sys.executable, "build-hybrid.py"], cwd=ROOT)

    # build-hybrid.py já valida cada fase, mas reconferimos o artefato final:
    # sem ele, o release empacotaria um instalador sem o executável.
    binary_name = "vectora.exe" if sys.platform == "win32" else "vectora"
    # --onedir: o executável fica em dist/vectora/ (pasta com _internal/).
    binary = os.path.join(ROOT, "dist", "vectora", binary_name)
    if not os.path.isfile(binary):
        raise SystemExit(
            f"ERRO: build-hybrid.py não gerou {binary}. "
            "Veja o passo do PyInstaller acima."
        )
    print(f">> executável do backend pronto em {binary}")


def _action_smoke(target, source, env):
    """Sobe o binário híbrido empacotado de verdade (não o source interpretado)
    e confirma que `/health` responde 200.

    O único smoke test que já existia (CI, job `release-native`) roda só
    `vectora --version` — confirma que o processo abre e fecha, não que o
    servidor FastAPI de fato sobe dentro do congelamento do PyInstaller
    (rotas montadas, settings carregadas, storage inicializado). Um binário
    que quebra nesse caminho só seria descoberto num release real sem isso.
    """
    binary_name = "vectora.exe" if sys.platform == "win32" else "vectora"
    binary = os.path.join(ROOT, "dist", "vectora", binary_name)
    if not os.path.isfile(binary):
        raise SystemExit(f"ERRO: {binary} não existe -- rode `scons release` primeiro.")

    port = 8781  # porta alta, fora do default (8080) para não colidir com um dev server local
    home_dir = tempfile.mkdtemp(prefix="vectora-smoke-")
    proc = subprocess.Popen(
        [binary, "web", "--port", str(port)],
        cwd=ROOT,
        env={**os.environ, "VECTORA_LICENSE_BYPASS": "1", "VECTORA_HOME": home_dir},
    )
    try:
        url = f"http://127.0.0.1:{port}/health"
        deadline = time.monotonic() + 30
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise SystemExit(
                    f"ERRO: binário empacotado saiu (código {proc.returncode}) "
                    "antes do /health responder -- ver stdout/stderr acima."
                )
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:  # nosec B310
                    if resp.status == 200:
                        print(f">> smoke test ok: {url} respondeu 200")
                        return
            except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
                last_error = exc
            time.sleep(1)
        raise SystemExit(f"ERRO: {url} não respondeu 200 em 30s (último erro: {last_error})")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(home_dir, ignore_errors=True)


def _find_signtool() -> str | None:
    if sys.platform != "win32":
        return None
    kits_root = r"C:\Program Files (x86)\Windows Kits\10\bin"
    if os.path.isdir(kits_root):
        for version_dir in sorted(os.listdir(kits_root), reverse=True):
            for arch in ("x64", "x86"):
                candidate = os.path.join(kits_root, version_dir, arch, "signtool.exe")
                if os.path.isfile(candidate):
                    return candidate
    return shutil.which("signtool.exe")


def _get_dev_cert_env() -> dict[str, str] | None:
    pfx_path = os.path.join(
        VECTORA, "frontend", "electron", "build-resources", "dev-cert.pfx"
    )
    if not os.path.isfile(pfx_path):
        return None
    password = os.environ.get("DEV_CSC_PASSWORD", "vectora-dev")
    with open(pfx_path, "rb") as f:
        pfx_b64 = base64.b64encode(f.read()).decode()
    return {"CSC_LINK": pfx_b64, "CSC_KEY_PASSWORD": password}


def _sign_binary(binary: str) -> None:
    pfx_path = os.path.join(
        VECTORA, "frontend", "electron", "build-resources", "dev-cert.pfx"
    )
    if not os.path.isfile(pfx_path) or not os.path.isfile(binary):
        return
    signtool = _find_signtool()
    if not signtool:
        print(">> signtool.exe nao encontrado — assinatura manual ignorada")
        return
    password = os.environ.get("DEV_CSC_PASSWORD", "vectora-dev")
    _run([signtool, "sign", "/f", pfx_path, "/p", password, "/fd", "SHA256", "/v", binary])
    print(f">> assinado: {os.path.basename(binary)}")


def _action_install_desktop(target, source, env):
    """Prepara o ambiente Python/Node e os sidecars usados no desenvolvimento."""
    _run([_find_bin("uv"), "sync", "--locked", "--all-groups"])
    # `pnpm ci` limpa node_modules e instala exatamente o lockfile do workspace.
    _run([PNPM, "--dir", "frontend", *PNPM_CI])
    _ensure_electron_binary()


def _ensure_electron_binary() -> None:
    """`pnpm install` só baixa o binário nativo do Electron via o
    postinstall do pacote npm `electron` — um download de rede que pode
    silenciosamente não completar (observado ao vivo: `path.txt` ausente
    mesmo após `pnpm install` reportar sucesso, sem nenhum erro visível).
    Sem esse binário, `electron_launcher.py` não encontra o executável e
    o backend cai pro modo web como fallback silencioso — inaceitável em
    dev/release, onde o objetivo explícito é o app desktop. `scons
    install` precisa garantir isso, não só confiar que o postinstall
    rodou.

    `pnpm rebuild electron` NÃO conserta isso de forma confiável — testado
    isoladamente (`path.txt` ausente antes, `pnpm rebuild electron` retorna
    exit 0, `path.txt` ainda ausente depois). O único mecanismo confirmado
    a baixar o binário de verdade é rodar o `install.js` do próprio pacote
    (o mesmo script que o postinstall invoca) diretamente via node."""
    path_txt = os.path.join(VECTORA, "frontend", "node_modules", "electron", "path.txt")
    if os.path.isfile(path_txt):
        return
    print(">> binário do Electron ausente (path.txt não encontrado) — rodando install.js")
    _run([
        PNPM, "--dir", "frontend", "exec", "node",
        "node_modules/electron/install.js",
    ])
    if not os.path.isfile(path_txt):
        print(
            "\n[scons frontend] Electron instalado mas o binário nativo não "
            "baixou (path.txt ausente mesmo após rodar install.js).\n"
            "  Verifique conexão de rede/proxy — sem isso o app desktop não "
            "pode rodar.\n"
        )
        raise SystemExit(1)


def _install_versions() -> dict[str, str | None]:
    """Versões das ferramentas que o ``install`` usa (``None`` = não responde)."""
    versions: dict[str, str | None] = {
        "python": f"Python {sys.version.split()[0]}",
        "uv": build_env.probe_version([_find_bin("uv"), "--version"]),
        "node": build_env.probe_version([_find_bin("node"), "--version"]),
        "pnpm": build_env.probe_version([PNPM, "--version"]),
        "git": build_env.probe_version([_find_bin("git"), "--version"]),
    }
    if sys.platform == "darwin":
        versions["swiftc"] = build_env.probe_version(
            [_find_bin("swiftc"), "--version"]
        )
    return versions


def _sidecar_labels(host) -> list[str]:
    """Sidecars que o ``install`` baixa ou compila para esta máquina."""
    labels: list[str] = []
    try:
        _url, member, _ext = _nats_asset()
        labels.append(member.split("/")[0])
    except RuntimeError as exc:
        labels.append(f"nats-server: {exc}")
    try:
        ffmpeg_url, _name = _ffmpeg_assets()[0]
        labels.append(f"ffmpeg/ffprobe ({os.path.basename(ffmpeg_url)})")
    except RuntimeError as exc:
        labels.append(f"ffmpeg/ffprobe: {exc}")
    if host.is_macos:
        labels.append("vectora-speech (fala nativa, compilado com swiftc)")
    return labels


def _exe(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def _installed_components(host, versions) -> list:
    """Confere no disco o que o ``install`` deve ter deixado pronto."""
    resources = os.path.join(VECTORA, "resources")
    venv_python = os.path.join(
        VECTORA,
        ".venv",
        "Scripts" if sys.platform == "win32" else "bin",
        _exe("python"),
    )
    electron_dir = os.path.join(VECTORA, "frontend", "node_modules", "electron")
    electron_binary = ""
    path_txt = os.path.join(electron_dir, "path.txt")
    if os.path.isfile(path_txt):
        with open(path_txt, encoding="utf-8") as handle:
            electron_binary = os.path.join(electron_dir, "dist", handle.read().strip())

    def binary(name: str, version_args: list[str]) -> build_env.Component:
        path = os.path.join(resources, _exe(name))
        if not os.path.isfile(path):
            return build_env.Component(name, "ausente", f"{path} não encontrado")
        version = build_env.probe_version([path, *version_args]) or ""
        return build_env.Component(
            name, "ok", f"{build_env.extract_version(version)} {path}".strip()
        )

    components = [
        build_env.Component(
            "Python (.venv)",
            "ok" if os.path.isfile(venv_python) else "ausente",
            venv_python,
        ),
        build_env.Component(
            "Frontend (node_modules)",
            "ok"
            if os.path.isdir(os.path.join(VECTORA, "frontend", "node_modules"))
            else "ausente",
            os.path.join(VECTORA, "frontend", "node_modules"),
        ),
        build_env.Component(
            "Electron (binário nativo)",
            "ok" if electron_binary and os.path.isfile(electron_binary) else "ausente",
            electron_binary or electron_dir,
        ),
        binary("nats-server", ["--version"]),
        binary("ffmpeg", ["-version"]),
        binary("ffprobe", ["-version"]),
    ]
    speech = os.path.join(resources, "vectora-speech")
    if host.is_macos:
        components.append(
            build_env.Component(
                "vectora-speech (fala do macOS)",
                "ok" if os.path.isfile(speech) else "ausente",
                speech,
            )
        )
    else:
        components.append(
            build_env.Component(
                "vectora-speech (fala do macOS)",
                "ignorado",
                f"não é macOS ({host.system})",
                required=False,
            )
        )
    return components


def _action_install(target, source, env):
    """Instala tudo o que o build de release usa, em passos numerados.

    1. Ambiente (detecta e informa; confere pré-requisitos antes de baixar).
    2. Dependências Python.  3. Dependências do frontend.  4. Electron nativo.
    5. nats-server.  6. ffmpeg e ffprobe.  7. Helper de fala (só no macOS).
    8. Verificação final, com a lista do que ficou instalado.

    Idempotente: binários já presentes em ``resources/`` são pulados.
    """
    total = 8
    host = build_env.detect_host()
    versions = _install_versions()

    def header(number: int, title: str) -> None:
        print(f"\n[{number}/{total}] {title}", flush=True)

    header(1, "Ambiente")
    missing, command = ([], "")
    if host.system == "Linux" and host.distro is not None:
        missing, command = build_env.packaging_hint(host.distro.family)
    print(
        build_env.render_environment(
            host, versions, _sidecar_labels(host), missing, command
        ),
        flush=True,
    )
    problems = build_env.missing_prerequisites(versions)
    if problems:
        print("\nFaltam pré-requisitos; nada foi baixado:")
        for problem in problems:
            print(f"  - {problem}")
        raise SystemExit(1)

    def step(number: int, title: str, action, retry: str) -> None:
        header(number, title)
        try:
            action()
        except (SystemExit, Exception) as exc:  # noqa: BLE001 - resumo antes de sair
            reason = exc if not isinstance(exc, SystemExit) else f"código {exc.code}"
            print(f"\nFalhou no passo {number}/{total} ({title}): {reason}")
            print(f"Repita só este passo com: {retry}")
            print("\nO que já está instalado:")
            print(build_env.render_components(_installed_components(host, versions)))
            raise SystemExit(1) from exc

    step(
        2,
        "Dependências Python (uv sync --locked --all-groups)",
        lambda: _run([_find_bin("uv"), "sync", "--locked", "--all-groups"]),
        "uv sync --locked --all-groups",
    )
    step(
        3,
        "Dependências do frontend (pnpm ci)",
        lambda: _run([PNPM, "--dir", "frontend", *PNPM_CI]),
        "pnpm --dir frontend ci",
    )
    step(4, "Electron (binário nativo)", _ensure_electron_binary, "scons install")
    step(
        5,
        "nats-server",
        lambda: _action_fetch_nats(None, None, None),
        "scons nats",
    )
    step(
        6,
        "ffmpeg e ffprobe",
        lambda: _action_fetch_ffmpeg(None, None, None),
        "scons ffmpeg",
    )
    if host.is_macos:
        step(
            7,
            "Helper de fala do macOS (swiftc)",
            lambda: _action_build_macos_speech(None, None, None),
            "scons macos-speech",
        )
    else:
        header(7, "Helper de fala do macOS")
        print(f"  ignorado: este sistema é {host.system}, não macOS")

    header(8, "Verificação")
    components = _installed_components(host, versions)
    print(build_env.render_components(components))
    if not build_env.components_ok(components):
        print("\nHá componentes obrigatórios ausentes; veja a lista acima.")
        raise SystemExit(1)
    print("\nPronto: o ambiente está completo para desenvolver e para o build de release.")


def _action_build_desktop(target, source, env):
    _run([PNPM, "--dir", "frontend", "run", "electron:build"])


def _free_desktop_dist() -> None:
    if sys.platform != "win32":
        return
    subprocess.run(
        ["taskkill", "/F", "/IM", "vectora.exe", "/T"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    unpacked = os.path.join(VECTORA, "frontend", "dist-electron", "win-unpacked")
    if os.path.isdir(unpacked):
        shutil.rmtree(unpacked, ignore_errors=True)
        print(">> limpou win-unpacked travado (lock prevention)")


def _action_package(target, source, env, platform=""):
    """Empaqueta com electron-builder escrevendo fora do repositório.

    O watcher de arquivos do editor/IDE trava o win-unpacked/resources/app.asar
    quando escrito dentro da árvore observada. Buildar num diretório externo
    elimina a corrida; ao final copiamos só os instaladores de volta.
    """
    _free_desktop_dist()
    out_dir = os.path.join(
        os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
        "Vectora",
        "dist-electron",
    )
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)

    flag = {"win": "--win", "mac": "--mac", "linux": "--linux"}.get(platform)
    cmd = [
        PNPM, "--dir", "frontend", "exec", "electron-builder",
        "--config", "electron/electron-builder.yml",
    ]
    if flag:
        cmd.append(flag)
    cmd.append(f"-c.directories.output={out_dir}")

    build_env: dict[str, str] = {}
    has_signing_creds = any(
        os.environ.get(k)
        for k in ("CSC_LINK", "WIN_CSC_LINK", "CSC_KEY_PASSWORD", "MAC_CSC_LINK")
    )
    if not has_signing_creds:
        dev_env = _get_dev_cert_env() if sys.platform == "win32" else None
        if dev_env:
            build_env.update(dev_env)
            backend_bin = os.path.join(ROOT, "dist", "vectora", "vectora.exe")
            if os.path.isfile(backend_bin):
                print(">> assinando o binário do backend (extraResource) com dev-cert.pfx...")
                _sign_binary(backend_bin)
            print(">> assinando instaladores com dev-cert.pfx")
        else:
            build_env["CSC_IDENTITY_AUTO_DISCOVERY"] = "false"
    _run(cmd, env=build_env or None)

    dest = os.path.join(VECTORA, "frontend", "dist-electron")
    os.makedirs(dest, exist_ok=True)
    patterns = (
        "*.exe", "*.msi", "*.dmg", "*.AppImage", "*.deb", "*.rpm",
        "latest*.yml", "*.blockmap",
    )
    copied: list[str] = []
    for pat in patterns:
        for f in glob.glob(os.path.join(out_dir, pat)):
            shutil.copy2(f, dest)
            copied.append(os.path.basename(f))
    if copied:
        print(f">> instaladores em {dest}:")
        for name in copied:
            print(f"     {name}")
    else:
        print(f">> build concluído em {out_dir} (nenhum instalador encontrado)")


# ── Lint ──────────────────────────────────────────────────────────────────────


def _pnpm_install_if_needed(pkg_dir: str, log) -> None:
    """Instala deps do subprojeto se node_modules ausente ou desatualizado."""
    modules = os.path.join(ROOT, pkg_dir, "node_modules")
    lock = os.path.join(ROOT, pkg_dir, "pnpm-lock.yaml")
    stamp = os.path.join(modules, ".pnpm-install-stamp")
    if (
        not os.path.isdir(modules)
        or not os.path.isfile(stamp)
        or (os.path.isfile(lock) and os.path.getmtime(lock) > os.path.getmtime(stamp))
    ):
        _run([PNPM, "--dir", pkg_dir, *PNPM_INSTALL], log=log)
        open(stamp, "w").close()  # noqa:


def _action_lint(target, source, env):
    with _open_log("lint") as log:
        # ── vectora: Python ───────────────────────────────────────────────────
        _run(["uv", "run", "ruff", "check", "backend", "tests"], log=log, cwd=VECTORA)
        _run(["uv", "run", "ty", "check", "backend", "tests"], log=log, cwd=VECTORA)
        _run(
            [
                "uv", "run", "python", "-m", "bandit",
                "-q",
                "-s", "B110,B101",
                "-c", "pyproject.toml",
                "--exclude", "backend/context_graph",
                "-r", "backend",
            ],
            log=log,
            cwd=VECTORA,
            discard_stderr=True,
        )
        # ── vectora: TypeScript frontend ──────────────────────────────────────
        # `typecheck` = i18n:compile (paraglide) + tsr generate + tsc --noEmit
        _run([PNPM, "--dir", "frontend", "run", "typecheck"], log=log)
        _run([PNPM, "--dir", "frontend", "exec", "oxlint"], log=log)
        # ── electron (main process, TS puro — sem oxlint/eslint configurado).
        # Fundido no package.json do frontend (mesmo node_modules); só o
        # tsconfig de compilação (Node/NodeNext) segue separado. ───────────
        _run(
            [PNPM, "--dir", "frontend", "exec", "tsc", "-p", "electron/tsconfig.json", "--noEmit"],
            log=log,
        )
    print("\n>> log completo (limpo) em .scons-logs/lint.txt")


# ── Tests ─────────────────────────────────────────────────────────────────────


def _action_tests_storage(target, source, env):
    """Testes de storage (Postgres, Redis, Qdrant, SQLite, LanceDB).

    Pulam automaticamente quando o serviço não está acessível.
    """
    with _open_log("tests-storage") as log:
        _run(
            [
                "uv", "run", "pytest", "tests/integration",
                "-q", "--tb=short",
                "-m", "storage",
            ],
            log=log,
            cwd=VECTORA,
        )
    print("\n>> log completo em .scons-logs/tests-storage.txt")


def _action_tests_live(target, source, env):
    """Testes sem mock que chamam LLM (Google/Cohere) e Tavily reais.

    Custam tempo/API real — nunca rodam como parte de `scons tests`. Pulam
    automaticamente quando a key correspondente não está configurada (mesmo
    padrão de skip-guard do resto da suíte).
    """
    with _open_log("tests-live") as log:
        _run(
            [
                "uv", "run", "pytest", "tests",
                "-q", "--tb=short",
                "-m", "live",
            ],
            log=log,
            cwd=VECTORA,
        )
    print("\n>> log completo em .scons-logs/tests-live.txt")


def _run_vectora_suite(log, *, coverage: bool):
    """Suíte do app: frontend (vitest, inclui electron fundido) + backend
    (pytest) — o mesmo que `.github/workflows/vectora.yml` cobre.

    Com ``coverage=True``, ativa --coverage no vitest e --cov no pytest
    (relatórios em frontend/coverage/ e htmlcov/).
    """
    # ── frontend ──────────────────────────────────────────────────────
    # Paraglide deve compilar antes do vitest: lib/paraglide é gitignored e o
    # vitest importa os messages compilados. O plugin Vite só compila em dev/build.
    _run([PNPM, "--dir", "frontend", "run", "i18n:compile"], log=log)

    # i18n:compile reescreve lib/paraglide/ do zero a cada chamada. O cache de
    # transform do Vite (node_modules/.vite) não invalida de forma confiável
    # quando o conteúdo muda por fora do próprio Vite — sobrava transform
    # velho pra alguns arquivos de mensagem, o vitest quebrava com "Failed to
    # resolve import ../runtime.js" de forma intermitente (reproduzido e
    # confirmado: limpar o cache resolve). Barato, então limpa sempre.
    frontend_vite_cache = os.path.join(VECTORA, "frontend", "node_modules", ".vite")
    shutil.rmtree(frontend_vite_cache, ignore_errors=True)

    # electron/src/__tests__/*.test.ts (cookie-utils, lifecycle puro) roda
    # junto — fundido no package.json do frontend, sem invocação separada;
    # vitest pega esses arquivos pelo include default (**/*.test.ts).
    vitest_cmd = [PNPM, "--dir", "frontend", "exec", "vitest", "run"]
    if coverage:
        vitest_cmd.append("--coverage")
    _run(vitest_cmd, log=log)

    # ── backend ───────────────────────────────────────────────────────
    # Testes live e benchmarks são opt-in; não entram no ciclo padrão.
    pytest_cmd = [
        "uv",
        "run",
        "pytest",
        "tests",
        # Exige o executável já empacotado (VECTORA_INSTALLED_EXECUTABLE) e roda
        # só pelo `vectora --tests` do app instalado.
        "--ignore=tests/installed_tests",
        "-q",
        "--tb=short",
        "-m",
        "not live and not benchmark",
    ]
    if coverage:
        pytest_cmd += [
            "--cov=backend",
            "--cov-report=term:skip-covered",
            "--cov-report=html:htmlcov",
        ]
    _run(pytest_cmd, log=log, cwd=VECTORA)


def _action_tests(target, source, env):
    with _open_log("tests") as log:
        _run_vectora_suite(log, coverage=False)
    print("\n>> log completo (limpo) em .scons-logs/tests.txt")


def _action_coverage(target, source, env):
    with _open_log("coverage") as log:
        _run_vectora_suite(log, coverage=True)
    print("\n>> log completo (limpo) em .scons-logs/coverage.txt")
    print(">> cobertura HTML: frontend/coverage/index.html, htmlcov/index.html")


# ── Update (deps) ─────────────────────────────────────────────────────────────


def _action_update(target, source, env):
    """Atualiza dependências de todos os subprojetos.

    Default: uv respeita os constraints do pyproject.toml (pin do Python 3.13
    intocado); `pnpm update` fica dentro dos ranges de cada package.json;

    `scons update --latest`: `pnpm update --latest` ignora os ranges do
    package.json e cruza majors (reescreve o package.json com a versão mais
    nova de cada pacote) — uso preparatório de migração grande, nunca rotina;
    depois é revisão manual por pacote major-bumped contra changelogs. Do lado
    Python, `uv lock --upgrade` resolve pro mais novo permitido pelos `>=` sem
    teto do pyproject.toml (não há range a "ignorar" lá).

    Não roda lint/tests ao final: o gate continua sendo
    `scons lint && scons tests`, manual, depois de revisar os lockfiles.
    """
    latest = bool(GetOption("latest"))
    pnpm_update_args = ["update", "--latest"] if latest else ["update"]
    with _open_log("update") as log:
        _run(["uv", "lock", "--upgrade"], log=log, cwd=VECTORA)
        _run(["uv", "sync"], log=log, cwd=VECTORA)
        _run([PNPM, "--dir", "frontend", *pnpm_update_args], log=log)
    print("\n>> log completo em .scons-logs/update.txt")
    if latest:
        print(
            ">> --latest: ranges/lockfiles ignorados — revise cada dep com "
            "major bump contra os changelogs antes de commitar"
        )
    else:
        print(">> revise uv.lock / frontend/pnpm-lock.yaml antes de commitar")


# ── Docker ────────────────────────────────────────────────────────────────────


def _action_docker(target, source, env):
    """Sobe a infraestrutura do Vectora (PostgreSQL, Redis, Qdrant).

    O Vectora em si NÃO roda como container. O backend roda no host.
    """
    probe = subprocess.run(
        ["docker", "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        cwd=ROOT,
        text=True,
        check=False,
    )
    daemon_error = (
        probe.returncode != 0
        or "failed to connect" in (probe.stderr or "").lower()
        or "cannot connect" in (probe.stderr or "").lower()
        or "is the docker daemon running" in (probe.stderr or "").lower()
    )
    if daemon_error:
        print(
            "\n[scons docker] Docker não está acessível.\n"
            "  Inicie o Docker Desktop e tente novamente.\n"
        )
        raise SystemExit(1)

    compose_env: dict[str, str] = {}
    dotenv = os.path.join(os.path.expanduser("~"), ".vectora", ".env")
    if os.path.isfile(dotenv):
        with open(dotenv, encoding="utf-8") as f:  # noqa: PTH123
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key, value = stripped.split("=", 1)
                    compose_env[key.strip()] = value.strip()

    _run(["docker", "compose", "up", "-d"], env=compose_env or None, cwd=VECTORA)
    print("\n>> Infraestrutura pronta. Configure ~/.vectora/.env:")
    print(">>   STORAGE_MODE=complete")
    print(">>   POSTGRES_DSN=postgresql+asyncpg://vectora:vectora@127.0.0.1:5432/vectora")
    print(">>   REDIS_URL=redis://127.0.0.1:6379/0")
    print(">>   QDRANT_URL=http://127.0.0.1:6333")
    print(">> Depois execute: vectora start")


# ── Clean ─────────────────────────────────────────────────────────────────────


def _action_clean(target, source, env):
    paths = [
        "dist",
        "build/pyinstaller",
        "build/vectora.spec",
        "frontend/dist",
        "frontend/.next",
        "frontend/out",
        "frontend/electron/dist",
        "frontend/dist-electron",
    ]
    for path in paths:
        full = os.path.join(ROOT, path.replace("/", os.sep))
        if os.path.isdir(full):
            shutil.rmtree(full)
            print(f">> removido: {path}")
        elif os.path.isfile(full):
            os.remove(full)
            print(f">> removido: {path}")


# ── Help ──────────────────────────────────────────────────────────────────────


def _action_help(target, source, env):
    sys.stdout.write("""
  Vectora — alvos SCons  (rodar da raiz do repositório)

  Produto final
    scons release          build completo + instalador para o SO atual
    scons smoke            roda o backend empacotado (PyInstaller) e confirma
                           que `/health` responde de dentro dele — não só `--version`

  Build
    scons frontend         só o build do frontend (frontend/dist/)
    scons install          prepara tudo: detecta o sistema e a arquitetura (e a distro no
                           Linux), sincroniza Python, pnpm ci, Electron, NATS, FFmpeg e,
                           só no macOS, o helper de fala; termina com a lista do instalado
    scons docker           sobe infraestrutura (PostgreSQL, Redis, Qdrant)

  Qualidade
    scons tests             frontend (vitest) + backend (pytest)
    scons coverage          scons tests COM relatório de cobertura
    scons tests-storage     só testes de storage (Postgres, Redis, Qdrant, SQLite, LanceDB)
    scons tests-live        só testes sem mock (LLM + Tavily reais) — custa API real
    scons lint             backend (ruff+ty+bandit) + frontend (tsc+oxlint)
    scons clean            remove todos os outputs de build

  Manutenção
    scons update           atualiza deps: uv (backend) + pnpm (frontend)
    scons update --latest  idem, mas ignora ranges/lockfiles (major bumps) —
                           só antes de migração grande, com revisão manual depois
    scons nats             baixa o binário nats-server pra resources/
                           (sidecar de fila/KV com JetStream; precisa de rede)
    scons ffmpeg           baixa ffmpeg+ffprobe pra resources/
                           (backend/tools/media_native.py; precisa de rede)
    scons macos-speech     compila o helper de fala nativo do macOS
                           (native/macos-speech, swiftc; só no macOS; o install já o faz)
""")
    sys.stdout.flush()


_NATS_VERSION = "2.10.22"

_FFMPEG_TAG = "b6.1.2-rc.1"


def _ffmpeg_assets() -> list[tuple[str, str]]:
    """``[(url, nome_do_arquivo_final), ...]`` — ffmpeg e ffprobe pra esta
    plataforma. Binários RAW (sem archive zip/tar, ao contrário do
    nats-server) de github.com/descriptinc/ffmpeg-ffprobe-static — mesmo
    projeto que ``ffmpeg-static``/``ffprobe-static`` do npm usam, cobre
    Windows/Linux x64+arm64 e macOS Apple Silicon. Windows ARM64 usa as
    ferramentas x64 sob emulação."""
    import platform as _plat

    sysname = {"windows": "win32", "linux": "linux", "darwin": "darwin"}.get(
        _plat.system().lower(), ""
    )
    if not sysname:
        raise RuntimeError(f"plataforma não suportada: {_plat.system()}")
    machine = _plat.machine().lower()
    # The pinned ffmpeg-ffprobe-static release does not publish Windows
    # ARM64 binaries. Windows ARM runs the x64 backend under emulation, so use
    # the matching x64 media tools there as well.
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    if sysname == "win32" and machine in ("arm64", "aarch64"):
        arch = "x64"
    is_win = sysname == "win32"

    base = (
        "https://github.com/descriptinc/ffmpeg-ffprobe-static/releases/download/"
        f"{_FFMPEG_TAG}/"
    )
    return [
        (
            f"{base}ffmpeg-{sysname}-{arch}",
            "ffmpeg.exe" if is_win else "ffmpeg",
        ),
        (
            f"{base}ffprobe-{sysname}-{arch}",
            "ffprobe.exe" if is_win else "ffprobe",
        ),
    ]


def _action_fetch_ffmpeg(target, source, env):
    """Baixa ffmpeg+ffprobe pra resources/ (media_native.py).

    Idempotente (pula cada binário já presente). Mesma resolução de destino
    que ``ffmpeg_binary.py::_resolve`` procura — sem os binários aqui, as
    tools de mídia local caem pro fallback de PATH do sistema (dev) e
    ficam indisponíveis em qualquer build empacotado sem ffmpeg instalado
    separadamente. Precisa de rede.
    """
    import urllib.request

    dest_dir = os.path.join(VECTORA, "resources")
    os.makedirs(dest_dir, exist_ok=True)

    for url, filename in _ffmpeg_assets():
        dest = os.path.join(dest_dir, filename)
        if os.path.isfile(dest):
            print(f">> {filename} já presente em {dest}")
            continue
        print(f">> baixando {filename}: {url}")
        with urllib.request.urlopen(url) as resp:  # noqa: S310 — release fixo do github
            data = resp.read()
        with open(dest, "wb") as out:
            out.write(data)
        if not filename.endswith(".exe"):
            os.chmod(dest, 0o755)
        print(f">> {filename} instalado em {dest}")


def _action_build_macos_speech(target, source, env):
    """Compila ``native/macos-speech/main.swift`` em ``resources/vectora-speech``.

    Helper de reconhecimento de fala nativo do macOS (framework Speech),
    usado por ``backend/services/speech_to_text.py``. Em outros sistemas não
    faz nada. Exige o swiftc das Command Line Tools do Xcode.
    """
    import platform as _plat
    import shutil as _shutil
    import subprocess as _subprocess

    if _plat.system() != "Darwin":
        print(">> macos-speech: ignorado fora do macOS")
        return 0
    swiftc = _shutil.which("swiftc")
    if swiftc is None:
        raise RuntimeError(
            "swiftc não encontrado; instale as Command Line Tools com "
            "`xcode-select --install`"
        )
    source_file = os.path.join(VECTORA, "native", "macos-speech", "main.swift")
    dest_dir = os.path.join(VECTORA, "resources")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "vectora-speech")
    _subprocess.run(  # noqa: S603
        [
            swiftc,
            "-O",
            "-swift-version",
            "5",
            "-framework",
            "Speech",
            "-o",
            dest,
            source_file,
        ],
        check=True,
    )
    os.chmod(dest, 0o755)
    print(f">> vectora-speech compilado em {dest}")
    return 0


def _nats_asset() -> tuple[str, str, str]:
    """``(url, membro_no_archive, ext)`` do nats-server para esta plataforma.

    Releases em github.com/nats-io/nats-server. Asset:
    ``nats-server-v{V}-{os}-{arch}.{zip|tar.gz}``; dentro fica
    ``nats-server-v{V}-{os}-{arch}/nats-server[.exe]``.
    """
    import platform as _plat

    sysname = {"windows": "windows", "linux": "linux", "darwin": "darwin"}.get(
        _plat.system().lower(), ""
    )
    if not sysname:
        raise RuntimeError(f"plataforma não suportada: {_plat.system()}")
    machine = _plat.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    ext = "zip" if sysname == "windows" else "tar.gz"
    stem = f"nats-server-v{_NATS_VERSION}-{sysname}-{arch}"
    url = (
        "https://github.com/nats-io/nats-server/releases/download/"
        f"v{_NATS_VERSION}/{stem}.{ext}"
    )
    binary = "nats-server.exe" if sysname == "windows" else "nats-server"
    return url, f"{stem}/{binary}", ext


def _action_fetch_nats(target, source, env):
    """Baixa o binário nats-server para resources/ (sidecar de fila/KV).

    Idempotente (pula se já existe). O sidecar (nats_sidecar._resolve_binary)
    procura exatamente esse caminho; sem o binário, cai pro fallback em memória
    e nada persiste. Precisa de rede.
    """
    import io
    import platform as _plat
    import tarfile
    import time
    import urllib.error
    import urllib.request
    import zipfile

    is_win = _plat.system().lower() == "windows"
    dest_dir = os.path.join(VECTORA, "resources")
    dest = os.path.join(dest_dir, "nats-server.exe" if is_win else "nats-server")
    if os.path.isfile(dest):
        print(f">> nats-server já presente em {dest}")
        return
    os.makedirs(dest_dir, exist_ok=True)

    url, member, ext = _nats_asset()
    print(f">> baixando nats-server: {url}")
    # GitHub Releases pode encerrar conexões durante downloads concorrentes da
    # matriz nativa. Repetir somente falhas transitórias evita reprovar o build
    # por uma interrupção de rede sem mascarar URLs/arquivos inválidos.
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(  # noqa: S310 — release fixo do github
                url, timeout=60
            ) as resp:
                data = resp.read()
            break
        except urllib.error.HTTPError as exc:
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == 4:
                raise
            print(
                f">> download NATS falhou (HTTP {exc.code}); "
                f"nova tentativa {attempt}/3"
            )
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == 4:
                raise
            print(
                f">> download NATS falhou ({exc}); "
                f"nova tentativa {attempt}/3"
            )
        time.sleep(2**attempt)

    if ext == "zip":
        with zipfile.ZipFile(io.BytesIO(data)) as zf, zf.open(member) as src:
            payload = src.read()
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            src = tf.extractfile(member)
            if src is None:
                raise RuntimeError(f"membro {member} ausente no archive")
            payload = src.read()

    temporary_dest = f"{dest}.download"
    try:
        with open(temporary_dest, "wb") as out:
            out.write(payload)
        os.replace(temporary_dest, dest)
    finally:
        if os.path.exists(temporary_dest):
            os.remove(temporary_dest)
    if not is_win:
        os.chmod(dest, 0o755)
    print(f">> nats-server instalado em {dest}")


# ── Alvos SCons ───────────────────────────────────────────────────────────────

env = Environment(ENV=os.environ)
env.Decider("timestamp-match")


def _node(name: str, action, deps: list | None = None):
    t = env.Command(f"_{name}", deps or [], action)
    env.AlwaysBuild(t)
    return t


def _cmd(name: str, action, deps: list | None = None):
    t = env.Command(f"_{name}", deps or [], action)
    env.AlwaysBuild(t)
    env.Alias(name, t)
    return t


def _action_build_chat_and_electron(target, source, env):
    """Compila a SPA e o código TypeScript do Electron sem instalar deps."""
    _action_build_chat(target, source, env)
    _action_build_desktop(target, source, env)


_cmd("frontend", _action_build_chat_and_electron)

# O binário nats-server precisa estar em resources/ ANTES tanto do
# PyInstaller (build-hybrid.py agora embute via --add-binary numa pasta
# nats/, pro standalone/VPS) quanto do electron-builder (extraResources, pro
# desktop) — sem esta dependência os dois builds saem sem o sidecar de
# fila/KV, caindo pra memória. _build_backend e _build_desktop dependem
# explicitamente, não só como irmãos em _FULL_DEPS (SCons não garante ordem
# entre irmãos sem edge de dependência).
_fetch_nats    = _node("fetch-nats",      _action_fetch_nats)
# Mesmo motivo do nats: ffmpeg/ffprobe precisam estar em resources/
# ANTES do PyInstaller (--add-binary) e do electron-builder (extraResources)
# empacotarem — sem isso, media_native.py cai pro fallback de PATH do
# sistema em qualquer build empacotado sem ffmpeg instalado separadamente.
_fetch_ffmpeg  = _node("fetch-ffmpeg",    _action_fetch_ffmpeg)
_cmd("install", _action_install)
# Helper de fala do macOS: o build-hybrid.py o exige no macOS; em outros
# sistemas a ação não faz nada, então o nó pode ficar no grafo de qualquer SO.
_fetch_speech  = _node("build-macos-speech", _action_build_macos_speech)
_inst_desktop  = _node("install-desktop", _action_install_desktop)
_build_chat    = _node("build-chat",      _action_build_chat,    deps=[_inst_desktop])
_build_backend = _node(
    "build-backend",
    _action_build_backend,
    deps=[_build_chat, _fetch_nats, _fetch_ffmpeg, _fetch_speech],
)
_build_desktop = _node(
    "build-desktop",
    _action_build_desktop,
    deps=[_build_chat, _inst_desktop, _fetch_nats, _fetch_ffmpeg],
)

_FULL_DEPS = [_build_chat, _fetch_nats, _fetch_ffmpeg, _build_backend, _build_desktop]

_cmd("release", lambda target, source, env: _action_package(target, source, env), deps=_FULL_DEPS)
# Sem deps=[_build_backend]: esse node é AlwaysBuild, então depender dele
# forçaria um novo empacotamento inteiro (caro) toda vez que `scons smoke`
# rodasse -- inclusive em CI, logo depois do binário já ter sido construído
# no passo anterior do workflow. `_action_smoke` já checa se o binário
# existe e falha com uma mensagem clara ("rode scons build-backend/release
# primeiro") se não existir.
_cmd("smoke", _action_smoke)

_cmd("tests",         _action_tests)
_cmd("coverage",      _action_coverage)
_cmd("tests-storage", _action_tests_storage)
_cmd("tests-live", _action_tests_live)
_cmd("lint",          _action_lint)
_cmd("update",        _action_update)
_cmd("nats",          _action_fetch_nats)
_cmd("ffmpeg",        _action_fetch_ffmpeg)
_cmd("macos-speech",  _action_build_macos_speech)
_cmd("clean",         _action_clean)
_cmd("help",          _action_help)
_cmd("docker",        _action_docker)

Default(env.Command("_default", [], _action_help))

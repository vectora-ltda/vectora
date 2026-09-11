"""
Vectora — SConstruct (SCons build file)

Uso (PowerShell / cmd, a partir da raiz do monorepo):
    scons               → exibe ajuda
    scons release       → build completo + instalador para o SO atual
    scons smoke         → builda o binário híbrido e confirma que /health sobe de verdade
    scons prod          → deploy de produção: docs + company (Vercel) + services (Worker)
    scons tests         → só o app Vectora (frontend + backend, sem cobertura)
    scons tests-edge    → docs (build) + company + services (sem cobertura)
    scons coverage      → scons tests com relatório de cobertura
    scons coverage-edge → scons tests-edge com relatório de cobertura
    scons lint          → todos os subprojetos: ruff+ty+bandit+tsc+oxlint
    scons update        → atualiza deps: uv (backend) + pnpm (frontend/company/
                          services) + hugo mod (docs), respeitando ranges/lockfiles
    scons update --latest → mesma coisa, mas ignora ranges/lockfiles (pnpm
                          --latest cruza majors) — usar só antes de uma migração
                          grande, seguido de revisão manual de cada breaking change
    scons docker        → sobe PostgreSQL + Redis + Qdrant via docker compose
    scons clean         → remove outputs de build

Pré-requisitos: uv, pnpm, nuitka (incluído no uv.lock), Hugo (extended) no PATH
Instalar SCons: pip install scons (ou uv add --dev scons)

Subprojetos cobertos por lint e tests:
    vectora/        Python (ruff, ty, bandit) + TS frontend (tsc, oxlint, vitest)
                    — inclui vectora/frontend/electron/ (Electron: cookie-utils
                    e lifecycle puro), fundido no package.json do frontend, sem
                    pacote npm próprio (só tsconfig de compilação separado)
    company/        TypeScript (oxlint, tsc, vitest)
    docs/           Hugo + Hextra (build check via `hugo --gc --minify`) — sem
                    testes; era Docusaurus, migrado pra Hugo
    services/       TypeScript (tsc, vitest) — gateway + updates unificados
                    (era relay/ (renomeado gateway) + update-server/, ver Fase A do plano de
                    unificação)
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

AddOption(
    "--latest",
    dest="latest",
    action="store_true",
    default=False,
    help="scons update --latest: ignora ranges/lockfiles, força major mais novo",
)

# ── Helpers ───────────────────────────────────────────────────────────────────

ROOT = Dir(".").abspath

# Subprojetos
VECTORA  = os.path.join(ROOT, "vectora")
COMPANY  = os.path.join(ROOT, "company")
DOCS     = os.path.join(ROOT, "docs")
SERVICES = os.path.join(ROOT, "services")

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _find_pnpm() -> str:
    if sys.platform == "win32":
        for name in ("pnpm.cmd", "pnpm.exe", "pnpm"):
            found = shutil.which(name)
            if found:
                return found
    return shutil.which("pnpm") or "pnpm"


def _find_bin(name: str) -> str:
    if sys.platform == "win32":
        for candidate in (f"{name}.cmd", f"{name}.exe", name):
            found = shutil.which(candidate)
            if found:
                return found
    return shutil.which(name) or name


PNPM = _find_pnpm()


def _find_hugo() -> str:
    found = shutil.which("hugo") or shutil.which("hugo.exe")
    if found:
        return found
    # winget instala fora do PATH da sessão atual até reiniciar o shell —
    # cai no caminho padrão do pacote Hugo.Hugo.Extended no Windows.
    if sys.platform == "win32":
        packages = os.path.join(
            os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages"
        )
        if os.path.isdir(packages):
            for name in os.listdir(packages):
                if name.startswith("Hugo.Hugo.Extended_"):
                    candidate = os.path.join(packages, name, "hugo.exe")
                    if os.path.isfile(candidate):
                        return candidate
    return "hugo"


HUGO     = _find_hugo()
VERCEL   = _find_bin("vercel")
WRANGLER = _find_bin("wrangler")


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
    header = f"\n>> [{os.path.relpath(run_cwd, ROOT)}] {' '.join(str(c) for c in cmd)}"
    print(header)
    stderr_dest = subprocess.DEVNULL if discard_stderr else None
    if log is None:
        result = subprocess.run(
            cmd, cwd=run_cwd, env=merged, stderr=stderr_dest, stdin=subprocess.DEVNULL
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
            sys.stdout.write(line)
            log.write(_ANSI_RE.sub("", line))
        proc.wait()
        rc = proc.returncode
    if rc != 0:
        raise SystemExit(rc)


def _open_log(name: str):
    logdir = os.path.join(ROOT, ".scons-logs")
    os.makedirs(logdir, exist_ok=True)
    return open(os.path.join(logdir, f"{name}.txt"), "w", encoding="utf-8")


# ── Ações de build ────────────────────────────────────────────────────────────


def _action_build_chat(target, source, env):
    frontend_dir = os.path.join(VECTORA, "frontend")
    node_env = {"NODE_NO_WARNINGS": "1"}
    _run([PNPM, "install", "--frozen-lockfile"], env=node_env, cwd=frontend_dir)
    _run([PNPM, "build"], env=node_env, cwd=frontend_dir)

    dist = os.path.join(VECTORA, "frontend", "dist")
    if not os.path.isdir(dist) or not os.path.isfile(os.path.join(dist, "index.html")):
        raise SystemExit(
            "ERRO: vectora/frontend/dist/index.html não foi gerado. "
            "Verifique a configuração do Vite em vectora/frontend/vite.config.ts."
        )
    print(f">> chat dist pronto em {dist}")


def _msvc_env() -> dict[str, str] | None:
    """Captura o ambiente do MSVC (x64) via vcvars64.bat, no Windows.

    O Nuitka ``--msvc=latest`` às vezes não configura o toolchain mesmo com MSVC +
    Windows SDK instalados (falha com "scons environment variable 'CC' is not
    set"). Pré-carregar o vcvars exporta INCLUDE/LIB/PATH e o Nuitka reaproveita o
    ambiente já ativo. O vcvars64.bat pode sair com código ≠ 0 por um aviso interno
    de vswhere mesmo tendo inicializado o ambiente — por isso validamos INCLUDE/LIB
    em vez do código de retorno. Retorna ``None`` (sem alterar o env) quando não há
    toolchain detectável, deixando o Nuitka tentar sua própria detecção.
    """
    if sys.platform != "win32":
        return None
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = os.path.join(
        program_files_x86, "Microsoft Visual Studio", "Installer", "vswhere.exe"
    )
    if not os.path.isfile(vswhere):
        return None
    try:
        install_path = subprocess.run(
            [vswhere, "-latest", "-products", "*", "-property", "installationPath"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None
    vcvars = os.path.join(install_path, "VC", "Auxiliary", "Build", "vcvars64.bat")
    if not install_path or not os.path.isfile(vcvars):
        return None
    # Forma string (não lista): o cmd precisa parsear o wrap externo de aspas
    # `cmd /c "..."` para lidar com o caminho do vcvars com espaços + o `&& set`.
    # A forma lista quebra o redirecionamento/aspas e não captura o ambiente.
    proc = subprocess.run(
        f'cmd /c ""{vcvars}" >nul 2>&1 && set"',
        capture_output=True,
        text=True,
    )
    env: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            env[key] = value
    if "INCLUDE" not in env or "LIB" not in env:
        return None
    # Força a saída do MSVC em inglês: o clcache do Nuitka parseia o output do
    # cl, e mensagens localizadas (pt-BR) disparam o aviso de "language pack".
    env["VSLANG"] = "1033"
    return env


def _action_build_nuitka(target, source, env):
    # Build híbrido: Nuitka --mode=package compila SÓ o pacote backend em C
    # (backend.pyd), depois PyInstaller empacota launcher + backend.pyd + libs
    # Python num vectora.exe. Compilar só o backend (pequeno) evita o OOM (C1002)
    # de compilar libs gigantes (google.genai.types ~157k linhas, lance) para C.
    # Default de jobs conservador (2); quem tem muita RAM sobe via NUITKA_JOBS.
    jobs = int(os.environ.get("NUITKA_JOBS", "2"))
    _run(
        [sys.executable, "build-hybrid.py", "--jobs", str(jobs)],
        cwd=ROOT,
        env=_msvc_env(),
    )

    # build-hybrid.py já valida cada fase, mas reconferimos o artefato final:
    # sem ele, o release empacotaria um instalador sem o executável.
    binary_name = "vectora.exe" if sys.platform == "win32" else "vectora"
    # --onedir: o executável fica em dist/vectora/ (pasta com _internal/).
    binary = os.path.join(ROOT, "dist", "vectora", binary_name)
    if not os.path.isfile(binary):
        raise SystemExit(
            f"ERRO: build-hybrid.py não gerou {binary}. "
            "Cheque o toolchain C (MSVC + Windows SDK) e o passo do PyInstaller."
        )
    print(f">> executável híbrido pronto em {binary}")


def _action_smoke(target, source, env):
    """Sobe o binário híbrido empacotado de verdade (não o source interpretado)
    e confirma que `/health` responde 200.

    O único smoke test que já existia (CI, job `release-native`) roda só
    `vectora --version` — confirma que o processo abre e fecha, não que o
    servidor FastAPI de fato sobe dentro do congelamento Nuitka+PyInstaller
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
                with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310  # nosec B310
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
    # Electron não tem package.json próprio — fundido em vectora/frontend/
    # (ver electron_launcher.py). Instalar aqui é o mesmo install do frontend;
    # existe como node separado só pra manter a árvore de dependências do
    # SConstruct explícita (_build_desktop depende deste node, não do
    # build-chat, que pode não ter rodado no pipeline de release).
    _run([PNPM, "--dir", "vectora/frontend", "install", "--frozen-lockfile"])
    _ensure_electron_binary()


def _ensure_electron_binary() -> None:
    """`pnpm install` só baixa o binário nativo do Electron via o
    postinstall do pacote npm `electron` — um download de rede que pode
    silenciosamente não completar (observado ao vivo: `path.txt` ausente
    mesmo após `pnpm install` reportar sucesso, sem nenhum erro visível).
    Sem esse binário, `electron_launcher.py` não encontra o executável e
    o backend cai pro modo web como fallback silencioso — inaceitável em
    dev/release, onde o objetivo explícito é o app desktop. `scons
    frontend` precisa garantir isso, não só confiar que o postinstall
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
        PNPM, "--dir", "vectora/frontend", "exec", "node",
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


def _action_build_desktop(target, source, env):
    _run([PNPM, "--dir", "vectora/frontend", "run", "electron:build"])


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
        PNPM, "--dir", "vectora/frontend", "exec", "electron-builder",
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
            nuitka_bin = os.path.join(ROOT, "dist", "vectora", "vectora.exe")
            if os.path.isfile(nuitka_bin):
                print(">> assinando binário híbrido (extraResource) com dev-cert.pfx...")
                _sign_binary(nuitka_bin)
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
        _run([PNPM, "--dir", pkg_dir, "install", "--frozen-lockfile"], log=log)
        open(stamp, "w").close()  # noqa: PTH123 WPS515


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
        _run([PNPM, "--dir", "vectora/frontend", "run", "typecheck"], log=log)
        _run([PNPM, "--dir", "vectora/frontend", "exec", "oxlint"], log=log)
        # ── company ───────────────────────────────────────────────────────────
        _pnpm_install_if_needed("company", log)
        _run([PNPM, "--dir", "company", "exec", "oxlint"], log=log)
        _run([PNPM, "--dir", "company", "run", "typecheck"], log=log)
        # ── electron (main process, TS puro — sem oxlint/eslint configurado).
        # Fundido no package.json do frontend (mesmo node_modules); só o
        # tsconfig de compilação (Node/NodeNext) segue separado. ───────────
        _run(
            [PNPM, "--dir", "vectora/frontend", "exec", "tsc", "-p", "electron/tsconfig.json", "--noEmit"],
            log=log,
        )
        # ── docs (Hugo + Hextra) ─────────────────────────────────────────────
        # Sem typecheck TS aqui — o gate é o próprio build do site. `hugo build`
        # já resolve o módulo Hextra pinado em go.mod sozinho — não rodar
        # `hugo mod get` aqui, que faz upgrade do pin como side-effect.
        _run([HUGO, "--gc", "--minify", "--destination", "public"], log=log, cwd=DOCS)
        # ── services (gateway + updates unificados) ──────────────────────────────
        _pnpm_install_if_needed("services", log)
        _run([PNPM, "--dir", "services", "exec", "tsc", "--noEmit"], log=log)
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
    """Suíte do app Vectora: frontend (vitest, inclui electron fundido) +
    backend (pytest). Ciclo próprio, mais rápido — só o que `.github/
    workflows/vectora.yml` cobre. Ver `_run_edge_suite` para docs/company/
    services (ciclo `edge.yml`).

    Com ``coverage=True``, ativa --coverage no vitest e --cov no pytest
    (relatórios em vectora/frontend/coverage/ e vectora/htmlcov/).
    """
    # ── vectora/frontend ──────────────────────────────────────────────────────
    # Paraglide deve compilar antes do vitest: lib/paraglide é gitignored e o
    # vitest importa os messages compilados. O plugin Vite só compila em dev/build.
    _run([PNPM, "--dir", "vectora/frontend", "run", "i18n:compile"], log=log)

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
    vitest_cmd = [PNPM, "--dir", "vectora/frontend", "exec", "vitest", "run"]
    if coverage:
        vitest_cmd.append("--coverage")
    _run(vitest_cmd, log=log)

    # ── vectora/backend ───────────────────────────────────────────────────────
    # "-m not live" exclui os testes sem mock que chamam LLM/Tavily reais
    # (marker `live`) — só rodam via `scons tests-live`, nunca aqui
    # (custariam API real a cada `scons tests`/`scons coverage`).
    pytest_cmd = ["uv", "run", "pytest", "tests", "-q", "--tb=short", "-m", "not live"]
    if coverage:
        pytest_cmd += [
            "--cov=backend",
            "--cov-report=term:skip-covered",
            "--cov-report=html:htmlcov",
        ]
    _run(pytest_cmd, log=log, cwd=VECTORA)


def _run_edge_suite(log, *, coverage: bool):
    """Suíte da borda web/edge: docs (Hugo, build-check — sem testes de
    verdade) + company (vitest) + services (vitest — gateway + updates
    unificados). Mesmo agrupamento de `.github/workflows/edge.yml`, num
    ciclo separado do app Vectora (`_run_vectora_suite`) — mais rápido pra
    quem só mexe no app, e vice-versa.

    Com ``coverage=True``, ativa --coverage no vitest de company/services
    (relatórios em company/coverage/ e services/coverage/).
    """
    # ── docs (Hugo + Hextra) ──────────────────────────────────────────────────
    # Sem testes de verdade — o gate é o próprio build do site (mesmo comando
    # de _action_lint). Incluído aqui porque faz parte do mesmo ciclo "edge".
    _run([HUGO, "--gc", "--minify", "--destination", "public"], log=log, cwd=DOCS)

    # ── company ───────────────────────────────────────────────────────────────
    # `exec vitest run` (não `run test --`): `pnpm run test -- --coverage`
    # repassa o "--" literal pro vitest em vez de servir só de separador —
    # confirmado ao vivo, a cobertura nunca ativava (mesmo padrão já usado
    # abaixo pro services).
    _pnpm_install_if_needed("company", log)
    company_test_cmd = [PNPM, "--dir", "company", "exec", "vitest", "run"]
    if coverage:
        company_test_cmd.append("--coverage")
    _run(company_test_cmd, log=log)

    # ── services (gateway + updates unificados; worker.ts + scripts/release.ts) ─
    _pnpm_install_if_needed("services", log)
    services_test_cmd = [PNPM, "--dir", "services", "exec", "vitest", "run"]
    if coverage:
        services_test_cmd.append("--coverage")
    _run(services_test_cmd, log=log)


def _action_tests(target, source, env):
    with _open_log("tests") as log:
        _run_vectora_suite(log, coverage=False)
    print("\n>> log completo (limpo) em .scons-logs/tests.txt")


def _action_tests_edge(target, source, env):
    with _open_log("tests-edge") as log:
        _run_edge_suite(log, coverage=False)
    print("\n>> log completo (limpo) em .scons-logs/tests-edge.txt")


def _action_coverage(target, source, env):
    with _open_log("coverage") as log:
        _run_vectora_suite(log, coverage=True)
    print("\n>> log completo (limpo) em .scons-logs/coverage.txt")
    print(">> cobertura HTML: vectora/frontend/coverage/index.html, vectora/htmlcov/index.html")


def _action_coverage_edge(target, source, env):
    with _open_log("coverage-edge") as log:
        _run_edge_suite(log, coverage=True)
    print("\n>> log completo (limpo) em .scons-logs/coverage-edge.txt")
    print(">> cobertura HTML: company/coverage/index.html, services/coverage/index.html")


# ── Update (deps) ─────────────────────────────────────────────────────────────


def _action_update(target, source, env):
    """Atualiza dependências de todos os subprojetos.

    Default: uv respeita os constraints do pyproject.toml (pin do Python 3.13
    intocado); `pnpm update` fica dentro dos ranges de cada package.json;
    `hugo mod get -u` atualiza o módulo Hextra pinado em docs/go.mod (o lint
    de propósito nunca faz esse upgrade — este é o único lugar que faz).

    `scons update --latest`: `pnpm update --latest` ignora os ranges do
    package.json e cruza majors (reescreve o package.json com a versão mais
    nova de cada pacote) — uso preparatório de migração grande, nunca rotina;
    depois é revisão manual por pacote major-bumped contra changelog/docs. Do
    lado Python, `uv lock --upgrade` já resolve pro mais novo permitido pelos
    `>=` sem teto do pyproject.toml (não há range a "ignorar" lá); Hugo idem
    (`-u` já busca o tag mais recente do módulo).

    Não roda lint/tests ao final: o gate continua sendo
    `scons lint && scons tests`, manual, depois de revisar os lockfiles.
    """
    latest = bool(GetOption("latest"))
    pnpm_update_args = ["update", "--latest"] if latest else ["update"]
    with _open_log("update") as log:
        _run(["uv", "lock", "--upgrade"], log=log, cwd=VECTORA)
        _run(["uv", "sync"], log=log, cwd=VECTORA)
        _run([PNPM, "--dir", "vectora/frontend", *pnpm_update_args], log=log)
        _run([HUGO, "mod", "get", "-u"], log=log, cwd=DOCS)
        _run([HUGO, "mod", "tidy"], log=log, cwd=DOCS)
        _run([PNPM, "--dir", "company", *pnpm_update_args], log=log)
        _run([PNPM, "--dir", "services", *pnpm_update_args], log=log)
    print("\n>> log completo em .scons-logs/update.txt")
    if latest:
        print(
            ">> --latest: ranges/lockfiles ignorados — revise cada dep com "
            "major bump contra changelog/docs antes de commitar"
        )
    else:
        print(">> revise uv.lock / pnpm-lock.yaml (x3) / go.mod+go.sum antes de commitar")


# ── Docker ────────────────────────────────────────────────────────────────────


def _action_docker(target, source, env):
    """Sobe a infraestrutura do Vectora (PostgreSQL, Redis, Qdrant).

    O Vectora em si NÃO roda como container. O backend roda no host.
    """
    probe = subprocess.run(  # noqa: S603 S607
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
        "vectora/dist-nuitka",
        "dist",
        "build/pyinstaller",
        "build/vectora.spec",
        "vectora/frontend/dist",
        "vectora/frontend/.next",
        "vectora/frontend/out",
        "vectora/frontend/electron/dist",
        "vectora/frontend/dist-electron",
        "docs/public",
        "docs/resources",
    ]
    for path in paths:
        full = os.path.join(ROOT, path.replace("/", os.sep))
        if os.path.isdir(full):
            shutil.rmtree(full)
            print(f">> removido: {path}")
        elif os.path.isfile(full):
            os.remove(full)
            print(f">> removido: {path}")


# ── Prod (deploy) ─────────────────────────────────────────────────────────────
# `scons prod` — deploy de produção da borda web/edge do monorepo: docs
# (Vercel, docs.vectora.company), company (Vercel, vectora.company) e services
# (Cloudflare Worker único: gateway + updates). Bump de versão, build do
# instalador e publicação no canal de update rodam só via GitHub Actions
# (.github/workflows/vectora.yml), disparados por "[up-release]" na mensagem
# do commit.
#
# Requer `vercel` e `wrangler` autenticados localmente (ambos já usados nesta
# máquina) e os projetos docs/company já linkados via `vercel link`
# (`.vercel/project.json` — gitignored, um link por máquina de dev).


def _check_wrangler_auth() -> None:
    """Falha cedo e claro se o wrangler não estiver autenticado na Cloudflare.

    Sem isto, um CLOUDFLARE_API_TOKEN inválido/sem escopo só estoura no meio do
    deploy (no `d1 migrations apply`), com erro críptico da API — e depois de
    já ter publicado docs+company no Vercel. Este preflight checa `whoami`
    antes de qualquer passo Cloudflare.
    """
    # encoding/errors explícitos: o wrangler imprime emoji (⛅️) em UTF-8, e o
    # `text=True` sozinho decodifica no code page do Windows (cp1252) — estoura
    # UnicodeDecodeError na thread de leitura do subprocess. Só olhamos o
    # returncode aqui, então errors="replace" basta.
    result = subprocess.run(
        [WRANGLER, "whoami"],
        cwd=SERVICES,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print(
            "\n[scons prod] wrangler não autenticado na Cloudflare.\n"
            "  O deploy do Worker (services) precisa de credencial válida com\n"
            "  permissão D1:Edit + Workers Scripts:Edit + R2:Edit.\n"
            "  Opções:\n"
            "    1) Regenere o CLOUDFLARE_API_TOKEN em\n"
            "       dash.cloudflare.com > My Profile > API Tokens (template\n"
            "       'Edit Cloudflare Workers' + adicione D1:Edit) e reexporte.\n"
            "    2) Ou rode `wrangler login` (OAuth) e REMOVA\n"
            "       CLOUDFLARE_API_TOKEN do ambiente (o env var tem prioridade\n"
            "       e sombreia o login).\n"
            "  Confirme com `wrangler whoami` antes de repetir `scons prod`.\n"
        )
        raise SystemExit(1)


def _check_vercel_link(folder: str, expected_project: str) -> None:
    """Falha cedo se a pasta não estiver linkada ao projeto Vercel CORRETO.

    `vercel --prod` publica no projeto gravado em `.vercel/project.json`
    (gitignored, um link por máquina de dev). Se a pasta foi linkada ao projeto
    errado — ex.: `company` em vez de `vectora-company`, o que acontece se você
    aceitar o autolink pelo nome da pasta em `vercel link` — o deploy vai pro
    lugar errado E o `scons prod` termina "sem erros", enquanto o domínio de
    produção (vectora.company / docs.vectora.company) segue servindo o deploy
    antigo/quebrado do projeto certo. Este preflight garante o alvo certo.
    """
    import json

    rel = os.path.relpath(folder, ROOT)
    link_path = os.path.join(folder, ".vercel", "project.json")
    if not os.path.isfile(link_path):
        print(
            f"\n[scons prod] {rel}/ não está linkado a nenhum projeto Vercel.\n"
            f"  Rode DENTRO de {rel}/:  vercel link\n"
            f"  e selecione o projeto '{expected_project}' (não o autolink pelo\n"
            f"  nome da pasta).\n"
        )
        raise SystemExit(1)
    with open(link_path, encoding="utf-8") as f:  # noqa: PTH123
        linked = json.load(f).get("projectName", "")
    if linked != expected_project:
        print(
            f"\n[scons prod] {rel}/ linkado ao projeto Vercel ERRADO.\n"
            f"  Linkado:   '{linked}'\n"
            f"  Esperado:  '{expected_project}'\n"
            f"  O deploy iria pro projeto errado e o domínio de produção\n"
            f"  continuaria servindo o deploy antigo. Corrija DENTRO de {rel}/:\n"
            f"    vercel link    (selecione '{expected_project}')\n"
        )
        raise SystemExit(1)


def _upgrade_d1_schema(log) -> None:
    """Adiciona colunas novas sem quebrar bancos D1 já existentes."""
    columns: tuple[tuple[str, str, str], ...] = (
        ("gha_bot_config", "self_hosted_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("issues", "github_repo", "TEXT"),
        ("issues", "github_number", "INTEGER"),
        ("issues", "github_url", "TEXT"),
        ("issues", "github_sync_state", "TEXT NOT NULL DEFAULT 'pending'"),
        ("issues", "github_sync_error", "TEXT"),
        ("issues", "core_repo", "TEXT"),
        ("issues", "core_number", "INTEGER"),
        ("issues", "core_url", "TEXT"),
        ("issues", "approved_at", "TEXT"),
        ("issues", "approved_by", "TEXT"),
        ("issues", "promotion_lease_until", "TEXT"),
        ("issues", "promotion_operation_token", "TEXT"),
        ("issues", "response_version", "INTEGER NOT NULL DEFAULT 0"),
        ("issues", "response_sync_lease_until", "TEXT"),
        ("rag_packages", "package_name", "TEXT"),
        ("rag_packages", "version", "TEXT NOT NULL DEFAULT '0.0.1'"),
        ("rag_packages", "status", "TEXT NOT NULL DEFAULT 'ready'"),
        ("rag_packages", "status_reason", "TEXT"),
        ("rag_packages", "embed_model", "TEXT"),
        ("rag_packages", "publisher_id", "TEXT"),
        ("rag_packages", "verified", "INTEGER NOT NULL DEFAULT 0"),
        ("rag_packages", "downloads_count", "INTEGER NOT NULL DEFAULT 0"),
        ("rag_packages", "license", "TEXT"),
        ("rag_packages", "description", "TEXT"),
        ("rag_packages", "updated_at", "TEXT"),
        ("skills_catalog", "package_name", "TEXT"),
        ("skills_catalog", "version", "TEXT NOT NULL DEFAULT '0.0.1'"),
        ("skills_catalog", "tags", "TEXT NOT NULL DEFAULT '[]'"),
        ("skills_catalog", "category", "TEXT"),
        ("skills_catalog", "catalog_source", "TEXT NOT NULL DEFAULT 'curated'"),
        ("skills_catalog", "vectora_verified", "INTEGER NOT NULL DEFAULT 0"),
        ("skills_catalog", "publisher_id", "TEXT"),
        ("skills_catalog", "verified", "INTEGER NOT NULL DEFAULT 0"),
        ("skills_catalog", "downloads_count", "INTEGER NOT NULL DEFAULT 0"),
        ("skills_catalog", "updated_at", "TEXT"),
        ("issue_comments", "updated_at", "TEXT"),
        ("issue_comments", "deleted_at", "TEXT"),
        ("github_webhook_deliveries", "attempt_token", "TEXT"),
        ("github_webhook_deliveries", "lease_until", "TEXT"),
        ("gha_bot_review_jobs", "callback_secret_hash", "TEXT"),
    )
    tables = {table for table, _, _ in columns}
    existing: dict[str, set[str]] = {}
    for table in tables:
        result = subprocess.run(
            [
                WRANGLER,
                "d1",
                "execute",
                "vectora-db",
                "--remote",
                "--command",
                f"PRAGMA table_info({table})",
                "--json",
            ],
            cwd=SERVICES,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            raise SystemExit(
                f"[scons prod] não foi possível consultar o schema D1: {table}"
            )
        existing[table] = set(
            re.findall(r'"name"\s*:\s*"([^"]+)"', result.stdout)
        )
    for table, column, definition in columns:
        if column in existing[table]:
            continue
        _run(
            [
                WRANGLER,
                "d1",
                "execute",
                "vectora-db",
                "--remote",
                "--command",
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}",
            ],
            log=log,
            cwd=SERVICES,
        )


def _action_prod(target, source, env):
    # Preflights ANTES de publicar qualquer coisa: credencial Cloudflare válida
    # e cada pasta linkada ao projeto Vercel certo — senão o deploy vai pro
    # lugar errado / o Worker fica pra trás, sem erro visível no scons.
    _check_wrangler_auth()
    _check_vercel_link(DOCS, "vectora-docs")
    _check_vercel_link(COMPANY, "vectora-company")
    with _open_log("prod") as log:
        _run([VERCEL, "--prod", "--yes"], log=log, cwd=DOCS)
        _run([VERCEL, "--prod", "--yes"], log=log, cwd=COMPANY)
        # Migrations ANTES do deploy do worker: o código deployado assume o
        # schema mais novo (ex.: users.role) — publicar worker sem aplicar as
        # migrations quebra rotas em produção com SQLITE_ERROR.
        # Sem flag de confirmação: `wrangler d1 migrations apply` moderno
        # detecta stdin não-TTY sozinho (`_run()` já redireciona pra
        # DEVNULL) e pula o prompt interativo automaticamente — uma flag
        # `--skip-confirmation` chegou a existir numa versão anterior do
        # wrangler mas não existe mais (`Unknown arguments`), então passá-la
        # quebra o comando em vez de proteger contra o prompt.
        #
        # `0001_schema.sql` fica FORA do rastreamento de `d1 migrations apply`
        # de propósito (arquivo único idempotente, reaplicado por completo a
        # cada mudança de schema — ver o cabeçalho do próprio arquivo). Isso
        # significa que `d1 migrations apply` sozinho NUNCA propaga uma tabela
        # nova adicionada ali pra produção — só os arquivos numerados em
        # `migrations/000N_*.sql` entram nesse rastreamento. Sem o `d1
        # execute` abaixo, uma tabela como `gha_bot_config` (adicionada a
        # `0001_schema.sql` no código, nunca reaplicada manualmente) fica
        # inexistente em produção indefinidamente, e a migration numerada que
        # depende dela (`ALTER TABLE gha_bot_config ADD COLUMN ...`) falha com
        # `no such table` no primeiro deploy que tentar rodá-la. Reaplicar
        # aqui, sempre, antes de `migrations apply`, é seguro (só `CREATE
        # TABLE/INDEX IF NOT EXISTS`) e elimina esse modo de falha de vez.
        _run(
            [
                WRANGLER,
                "d1",
                "execute",
                "vectora-db",
                "--remote",
                "--file=migrations/0001_schema.sql",
            ],
            log=log,
            cwd=SERVICES,
        )
        _run(
            [
                WRANGLER,
                "d1",
                "migrations",
                "apply",
                "vectora-db",
                "--remote",
            ],
            log=log,
            cwd=SERVICES,
        )
        # O upgrade aditivo só roda depois das migrations, que criam as
        # tabelas auxiliares consultadas pelo preflight.
        _upgrade_d1_schema(log)
        _run(
            [
                WRANGLER,
                "d1",
                "execute",
                "vectora-db",
                "--remote",
                "--command",
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_issues_github_identity ON issues(github_repo, github_number) WHERE github_repo IS NOT NULL AND github_number IS NOT NULL",
            ],
            log=log,
            cwd=SERVICES,
        )
        _run([WRANGLER, "deploy"], log=log, cwd=SERVICES)
    print(
        "\n>> deploy de produção concluído: "
        "docs.vectora.company + vectora.company + services (Cloudflare Worker)"
    )
    print(">> log completo em .scons-logs/prod.txt")


# ── Help ──────────────────────────────────────────────────────────────────────


def _action_help(target, source, env):
    sys.stdout.write("""
  Vectora — alvos SCons  (rodar da raiz do monorepo)

  Produto final
    scons release          build completo + instalador para o SO atual
    scons prod             deploy de produção: docs + company (Vercel) + services (Worker)
    scons smoke            builda o binário híbrido (Nuitka+PyInstaller) e confirma
                           que `/health` responde de dentro dele — não só `--version`

  Build
    scons frontend         só o build do frontend (vectora/frontend/dist/)
    scons docker           sobe infraestrutura (PostgreSQL, Redis, Qdrant)

  Qualidade
    scons tests             só o app Vectora: frontend (vitest) + backend (pytest)
    scons tests-edge        docs (build Hugo) + company (vitest) + services (vitest)
    scons coverage          scons tests COM relatório de cobertura
    scons coverage-edge     scons tests-edge COM relatório de cobertura
    scons tests-storage     só testes de storage (Postgres, Redis, Qdrant, SQLite, LanceDB)
    scons tests-live        só testes sem mock (LLM + Tavily reais) — custa API real
    scons lint             vectora (ruff+ty+bandit+tsc+oxlint) + company (eslint+tsc)
                           + docs (tsc) + services (tsc)
    scons clean            remove todos os outputs de build

  Manutenção
    scons update           atualiza deps: uv (backend) + pnpm (frontend,
                           company, services) + hugo mod (docs)
    scons update --latest  idem, mas ignora ranges/lockfiles (major bumps) —
                           só antes de migração grande, com revisão manual depois
    scons nats             baixa o binário nats-server pra vectora/resources/
                           (sidecar de fila/KV com JetStream; precisa de rede)
    scons ffmpeg           baixa ffmpeg+ffprobe pra vectora/resources/
                           (backend/tools/media_native.py; precisa de rede)
""")
    sys.stdout.flush()


_NATS_VERSION = "2.10.22"

_FFMPEG_TAG = "b6.1.2-rc.1"


def _ffmpeg_assets() -> list[tuple[str, str]]:
    """``[(url, nome_do_arquivo_final), ...]`` — ffmpeg e ffprobe pra esta
    plataforma. Binários RAW (sem archive zip/tar, ao contrário do
    nats-server) de github.com/descriptinc/ffmpeg-ffprobe-static — mesmo
    projeto que ``ffmpeg-static``/``ffprobe-static`` do npm usam, cobre
    Windows/macOS/Linux x64+arm64 (sem Windows arm64, raro o bastante pra
    não bloquear)."""
    import platform as _plat

    sysname = {"windows": "win32", "linux": "linux", "darwin": "darwin"}.get(
        _plat.system().lower(), ""
    )
    if not sysname:
        raise RuntimeError(f"plataforma não suportada: {_plat.system()}")
    machine = _plat.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
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
    """Baixa ffmpeg+ffprobe pra vectora/resources/ (media_native.py).

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
    """Baixa o binário nats-server para vectora/resources/ (sidecar de fila/KV).

    Idempotente (pula se já existe). O sidecar (nats_sidecar._resolve_binary)
    procura exatamente esse caminho; sem o binário, cai pro fallback em memória
    e nada persiste. Precisa de rede.
    """
    import io
    import platform as _plat
    import tarfile
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
    with urllib.request.urlopen(url) as resp:  # noqa: S310 — release fixo do github
        data = resp.read()

    if ext == "zip":
        with zipfile.ZipFile(io.BytesIO(data)) as zf, zf.open(member) as src:
            payload = src.read()
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            src = tf.extractfile(member)
            if src is None:
                raise RuntimeError(f"membro {member} ausente no archive")
            payload = src.read()

    with open(dest, "wb") as out:
        out.write(payload)
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
    """`scons frontend` builda a SPA e também o dev build do Electron
    (`pnpm --dir frontend install` + `run electron:build`, gerando
    `frontend/electron/dist/main.js` e baixando o binário real do pacote npm
    `electron` pro node_modules do frontend). Electron-first em dev
    (`backend/services/electron_launcher.py`) depende dos dois existirem pro
    backend conseguir se autoeleger e spawnar o Electron sozinho quando
    `uv run vectora start` roda direto, fora do Electron."""
    _action_build_chat(target, source, env)
    _action_install_desktop(target, source, env)
    _action_build_desktop(target, source, env)


_cmd("frontend", _action_build_chat_and_electron)

_build_chat    = _node("build-chat",      _action_build_chat)
# O binário nats-server precisa estar em vectora/resources/ ANTES tanto do
# PyInstaller (build-hybrid.py agora embute via --add-binary numa pasta
# nats/, pro standalone/VPS) quanto do electron-builder (extraResources, pro
# desktop) — sem esta dependência os dois builds saem sem o sidecar de
# fila/KV, caindo pra memória. _build_nuitka e _build_desktop dependem
# explicitamente, não só como irmãos em _FULL_DEPS (SCons não garante ordem
# entre irmãos sem edge de dependência).
_fetch_nats    = _node("fetch-nats",      _action_fetch_nats)
# Mesmo motivo do nats: ffmpeg/ffprobe precisam estar em vectora/resources/
# ANTES do PyInstaller (--add-binary) e do electron-builder (extraResources)
# empacotarem — sem isso, media_native.py cai pro fallback de PATH do
# sistema em qualquer build empacotado sem ffmpeg instalado separadamente.
_fetch_ffmpeg  = _node("fetch-ffmpeg",    _action_fetch_ffmpeg)
_build_nuitka  = _node("build-nuitka",    _action_build_nuitka,   deps=[_build_chat, _fetch_nats, _fetch_ffmpeg])
_inst_desktop  = _node("install-desktop", _action_install_desktop)
_build_desktop = _node("build-desktop",   _action_build_desktop,  deps=[_inst_desktop, _fetch_nats, _fetch_ffmpeg])

_FULL_DEPS = [_build_chat, _fetch_nats, _fetch_ffmpeg, _build_nuitka, _build_desktop]

_cmd("release", lambda target, source, env: _action_package(target, source, env), deps=_FULL_DEPS)
# Sem deps=[_build_nuitka]: esse node é AlwaysBuild, então depender dele
# forçaria uma recompilação Nuitka inteira (cara) toda vez que `scons smoke`
# rodasse -- inclusive em CI, logo depois do binário já ter sido construído
# no passo anterior do workflow. `_action_smoke` já checa se o binário
# existe e falha com uma mensagem clara ("rode scons build-nuitka/release
# primeiro") se não existir.
_cmd("smoke", _action_smoke)

_cmd("prod",           _action_prod)

_cmd("tests",         _action_tests)
_cmd("tests-edge",    _action_tests_edge)
_cmd("coverage",      _action_coverage)
_cmd("coverage-edge", _action_coverage_edge)
_cmd("tests-storage", _action_tests_storage)
_cmd("tests-live", _action_tests_live)
_cmd("lint",          _action_lint)
_cmd("update",        _action_update)
_cmd("nats",          _action_fetch_nats)
_cmd("ffmpeg",        _action_fetch_ffmpeg)
_cmd("clean",         _action_clean)
_cmd("help",          _action_help)
_cmd("docker",        _action_docker)

Default(env.Command("_default", [], _action_help))

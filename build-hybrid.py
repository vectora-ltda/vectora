#!/usr/bin/env python
"""Build híbrido: Nuitka compila o pacote backend -> backend.pyd; PyInstaller
empacota launcher + backend.pyd + libs Python -> vectora.exe.

Uso:  python build-hybrid.py [--jobs N]   (NUITKA_JOBS=N também funciona)
"""

import os
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path

ROOT = Path(__file__).parent
VECTORA = ROOT
SEP = os.pathsep

# Libs que o backend importa e que o PyInstaller NÃO vê (backend.pyd é opaco).
# Confirmar/expandir com o smoke test do .exe.
COLLECT_ALL = [
    "mcp",
    "fastapi",
    "starlette",
    "uvicorn",
    "pydantic",
    "pydantic_core",
    "google",
    "lancedb",
    "aiosqlite",
    "httpx",
    # tiktoken usa um sistema de plugins via namespace package: o registry faz
    # pkgutil.iter_modules(tiktoken_ext.__path__) para descobrir os encoders.
    # PyInstaller não vê esse import dentro do backend.pyd compilado.
    "tiktoken",
    "tiktoken_ext",
]

# Módulos que precisam de hidden-import adicional além do collect-all.
# tiktoken_ext.openai_public é o plugin que define cl100k_base, p50k_base etc.;
# em ambiente congelado pkgutil.iter_modules pode não enumerar namespace packages
# automaticamente — o hidden-import garante que o módulo entre no bundle.
HIDDEN_IMPORTS = [
    "tiktoken_ext.openai_public",
]


def run(cmd: list, cwd: Path, desc: str, env: dict | None = None) -> None:
    print(f"\n{'=' * 70}\n-> {desc}\n{'=' * 70}\n$ {' '.join(map(str, cmd))}\n")
    merged = {**os.environ, **(env or {})}
    if subprocess.run(cmd, cwd=str(cwd), env=merged).returncode != 0:  # noqa: S603  # nosec B603
        sys.exit(f"FALHA: {desc}")


def _add_installed_test_runtime(dist_dir: Path) -> None:
    """Ship uv and the black-box suite; pytest itself stays opt-in/downloaded."""
    uv_binary = shutil.which("uv")
    if not uv_binary:
        sys.exit("uv não encontrado; o runtime instalado precisa dele para --tests.")
    suite = VECTORA / "tests" / "installed_tests" / "test_installed_runtime.py"
    if not suite.is_file():
        sys.exit(f"Suíte de instalação não encontrada: {suite}")
    pytest_config = suite.parent / "pytest.ini"
    if not pytest_config.is_file():
        sys.exit(f"Configuração da suíte de instalação não encontrada: {pytest_config}")

    runtime_dir = dist_dir / "vectora-test-runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    uv_name = "uv.exe" if sys.platform == "win32" else "uv"
    shutil.copy2(uv_binary, runtime_dir / uv_name)
    shutil.copy2(suite, runtime_dir / suite.name)
    shutil.copy2(pytest_config, runtime_dir / pytest_config.name)
    print(f"Runtime de teste instalado preparado em {runtime_dir}")


# Padrões de arquivo que nunca podem existir dentro de backend/ na
# hora do build: o Nuitka `--mode=package` embute QUALQUER arquivo não-.py
# achado dentro do pacote (é assim que backend/defaults.env sobrevive ao
# empacotamento via importlib.resources — confirmado: nem aparece como
# arquivo solto no dist/vectora/ gerado, só funciona embutido no .pyd). Um
# `.env`/chave/credencial esquecido ali vira permanente e invisível dentro do
# binário entregue a QUALQUER usuário que baixar o instalador — diferente de
# .env (fora do pacote), que nunca é varrido pelo Nuitka.
_FORBIDDEN_BACKEND_FILE_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "id_rsa*",
    "credentials.json",
    "service-account*.json",
)


def _assert_no_secrets_inside_backend() -> None:
    backend_dir = VECTORA / "backend"
    allowed = {backend_dir / "defaults.env"}
    found: set[Path] = set()
    for pattern in _FORBIDDEN_BACKEND_FILE_PATTERNS:
        found.update(p for p in backend_dir.rglob(pattern) if p.is_file())
    found -= allowed
    if found:
        listed = "\n".join(f"  - {p.relative_to(VECTORA)}" for p in sorted(found))
        sys.exit(
            "FALHA: arquivo de segredo dentro de backend/ — o Nuitka "
            "embute isso no binário compilado, entregue a todo mundo que "
            f"baixar o instalador:\n{listed}\n"
            "Mova pra .env (fora do pacote) ou ~/.vectora/.env antes "
            "de rodar o build."
        )


def main() -> None:
    _assert_no_secrets_inside_backend()
    jobs = os.environ.get("NUITKA_JOBS", "2")
    if "--jobs" in sys.argv:
        jobs = sys.argv[sys.argv.index("--jobs") + 1]

    # No macOS, o binário "gcc" do PATH é na real o clang da Apple (alias
    # histórico) — sem --clang, o Nuitka detecta "gcc" e passa flags
    # exclusivas de GNU GCC (-fpartial-inlining, -ftrack-macro-expansion=0,
    # -fno-var-tracking-assignments) que o clang rejeita, quebrando a
    # compilação Scons. --clang força o Nuitka a gerar as flags certas.
    clang_flag = ["--clang"] if sys.platform == "darwin" else []

    # Etapa 1 — Nuitka: pacote backend -> backend.pyd (só backend vira C)
    run(
        [
            "uv",
            "run",
            "nuitka",
            "--mode=package",
            "--msvc=latest",
            *clang_flag,
            f"--jobs={jobs}",
            f"--output-dir={VECTORA / 'dist-nuitka'}",
            "--include-module=backend.services.ipc_pipe_win",
            f"--report={ROOT / 'nuitka-report.xml'}",
            "backend",
        ],
        cwd=VECTORA,
        desc="Nuitka --mode=package backend -> backend.pyd",
    )
    # Nuitka nomeia o módulo de extensão pela convenção do CPython: .pyd no
    # Windows, .so no Linux e no macOS (extensão importável — .dylib é só
    # pra bibliotecas compartilhadas comuns, não módulos de import).
    ext = "pyd" if sys.platform == "win32" else "so"
    pyd = next((VECTORA / "dist-nuitka").glob(f"backend*.{ext}"), None)
    if not pyd:
        sys.exit(f"backend.{ext} não gerado")
    print(f"OK: {pyd}")

    # nats-server precisa estar em resources/ ANTES do PyInstaller
    # rodar (baixado por `scons nats`/_fetch_nats) — sem ele, o binário
    # standalone sobe sem sidecar de fila/KV e get_mq()/get_kv() degradam pro
    # fallback em memória (nunca falha o boot, mas silenciosamente perde
    # persistência em produção). backend.scheduling.nats_sidecar resolve o
    # binário embutido em runtime via sys._MEIPASS (mesmo mecanismo do
    # chat_static abaixo).
    nats_exe = "nats-server.exe" if sys.platform == "win32" else "nats-server"
    nats_binary = VECTORA / "resources" / nats_exe
    if not nats_binary.is_file():
        sys.exit(
            f"{nats_binary} não encontrado. Rode `scons nats` antes do build "
            "(baixa o binário nats-server pra resources/)."
        )

    # ffmpeg/ffprobe — mesmo motivo/mecanismo do nats-server acima
    # (baixados por `scons ffmpeg`, resolvidos em runtime via
    # backend.services.ffmpeg_binary._frozen_bundle_bases, mesmo padrão de
    # sys._MEIPASS). Sem eles, backend/tools/media_native.py degrada pro
    # fallback de PATH do sistema — silencioso, não falha o boot.
    ffmpeg_exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    ffprobe_exe = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    ffmpeg_binary = VECTORA / "resources" / ffmpeg_exe
    ffprobe_binary = VECTORA / "resources" / ffprobe_exe
    if not ffmpeg_binary.is_file() or not ffprobe_binary.is_file():
        sys.exit(
            f"{ffmpeg_binary}/{ffprobe_binary} não encontrados. Rode `scons "
            "ffmpeg` antes do build (baixa ffmpeg+ffprobe pra "
            "resources/)."
        )

    # Helper de fala nativo do macOS (framework Speech), compilado por
    # `scons macos-speech`; resolvido em runtime por
    # backend.services.speech_to_text (pasta speech/ do bundle).
    speech_args: list[str] = []
    if sys.platform == "darwin":
        speech_binary = VECTORA / "resources" / "vectora-speech"
        if not speech_binary.is_file():
            sys.exit(
                f"{speech_binary} não encontrado. Rode `scons macos-speech` "
                "antes do build (compila o helper de fala do macOS)."
            )
        speech_args = ["--add-binary", f"{speech_binary}{SEP}speech"]

    collect: list[str] = []
    for pkg in COLLECT_ALL:
        collect += ["--collect-all", pkg]
    # --collect-all NÃO copia o dist-info do próprio pacote "vectora" (só
    # código/dados/binários) — sem os metadados, importlib.metadata.version
    # ("vectora") levanta PackageNotFoundError dentro do binário congelado e
    # backend.version.get_vectora_version() cai silenciosamente no fallback
    # "0.1.0", relatando a versão errada no log de startup.
    collect += ["--copy-metadata", "vectora"]
    hidden: list[str] = []
    for mod in HIDDEN_IMPORTS:
        hidden += ["--hidden-import", mod]

    # Módulos declarados pelos hooks do PyInstaller mas não instalados no projeto.
    # Excluir explicitamente para silenciar os "Hidden import X not found!" warnings.
    exclude_modules = [
        "pycparser.lextab",  # gerado em runtime pelo pycparser, não existe no venv
        "pycparser.yacctab",  # idem
        "pysqlite2",  # dialeto opcional do SQLAlchemy
        "MySQLdb",  # dialeto opcional do SQLAlchemy
    ]
    excludes: list[str] = []
    for mod in exclude_modules:
        excludes += ["--exclude-module", mod]

    # Etapa 2 — PyInstaller: launcher + backend.pyd + libs -> vectora.exe
    run(
        [
            "uv",
            "run",
            "python",
            "-m",
            "PyInstaller",
            # --onedir (pasta) em vez de --onefile: os arquivos ficam soltos e
            # SEM compressão, então entre versões as libs/DLLs não-alteradas ficam
            # byte-idênticas → o blockmap do electron-updater baixa só o que mudou
            # (delta real de poucos MB, não 1.5GB). Não muda a segurança: backend.pyd
            # continua C, libs continuam Python bytecode.
            "--onedir",
            "--noconfirm",
            "--name=vectora",
            f"--distpath={ROOT / 'dist'}",
            f"--workpath={ROOT / 'build' / 'pyinstaller'}",
            f"--specpath={ROOT / 'build'}",
            "--add-binary",
            f"{pyd}{SEP}.",
            "--add-binary",
            f"{nats_binary}{SEP}nats",
            "--add-binary",
            f"{ffmpeg_binary}{SEP}ffmpeg",
            "--add-binary",
            f"{ffprobe_binary}{SEP}ffmpeg",
            *speech_args,
            "--add-data",
            f"{VECTORA / 'frontend' / 'dist'}{SEP}chat_static",
            "--add-data",
            f"{VECTORA / 'backend' / 'assets'}{SEP}backend/assets",
            "--add-data",
            # DEST é sempre tratado como diretório pelo PyInstaller -- um SRC
            # de arquivo único é copiado PARA DENTRO dele com o próprio nome.
            # Terminar DEST em "schema.sql" cria uma pasta chamada schema.sql
            # e põe o arquivo dentro dela (.../schema.sql/schema.sql), não o
            # arquivo em si -- por isso migrations falhava silenciosamente no
            # binário empacotado (FileNotFoundError engolido, ver
            # backend/storage/migrations/runner.py). DEST precisa ser o
            # diretório PAI.
            f"{VECTORA / 'backend' / 'storage' / 'migrations' / 'sqlite' / 'schema.sql'}{SEP}backend/storage/migrations/sqlite",
            *collect,
            *hidden,
            *excludes,
            str(VECTORA / "launcher.py"),
        ],
        cwd=VECTORA,
        desc="PyInstaller -> vectora.exe",
        # Suprime SyntaxWarning do pacote `future` (escape sequences inválidas
        # em ficheiros de backports que o PyInstaller importa durante a análise).
        env={"PYTHONWARNINGS": "ignore::SyntaxWarning"},
    )
    # --onedir gera a pasta dist/vectora/ com vectora[.exe] + _internal/.
    exe_name = "vectora.exe" if sys.platform == "win32" else "vectora"
    exe = ROOT / "dist" / "vectora" / exe_name
    if not exe.exists():
        sys.exit(f"{exe} não gerado")
    dist_dir = ROOT / "dist" / "vectora"

    # Confere que o nats-server embutido sobreviveu ao empacotamento — sem
    # isso o build "passa" mas o sidecar de fila/KV degrada silenciosamente
    # pra memória em produção (ver nats_sidecar._frozen_bundle_bases).
    if not any(dist_dir.rglob(f"nats/{nats_exe}")):
        sys.exit(
            f"nats/{nats_exe} não apareceu em {dist_dir} após o PyInstaller "
            "— o sidecar de fila/KV vai degradar pra memória em produção."
        )
    if not any(dist_dir.rglob(f"ffmpeg/{ffmpeg_exe}")) or not any(
        dist_dir.rglob(f"ffmpeg/{ffprobe_exe}")
    ):
        sys.exit(
            f"ffmpeg/{ffmpeg_exe} ou ffmpeg/{ffprobe_exe} não apareceram em "
            f"{dist_dir} após o PyInstaller — as tools de mídia local vão "
            "degradar pro fallback de PATH do sistema em produção."
        )
    _add_installed_test_runtime(dist_dir)
    total_mb = (
        sum(f.stat().st_size for f in dist_dir.rglob("*") if f.is_file()) / 1048576
    )
    print(f"\nBUILD OK — {dist_dir}  ({total_mb:.1f} MB na pasta)")


if __name__ == "__main__":
    main()

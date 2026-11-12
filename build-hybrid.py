#!/usr/bin/env python
"""Build do backend: o PyInstaller empacota o launcher, o pacote `backend` (código
e arquivos de dados) e as libs Python em `dist/vectora/` (--onedir).

O backend vai como bytecode Python, sem compilação em C. O PyInstaller
coleta todos os submódulos e os dados de `backend/` (`--collect-submodules` e
`--collect-data`), o que inclui `backend/defaults.env`. Tudo isso vai legível
para o instalador, então o build recusa segredos no pacote e em `defaults.env`.

Uso:  python build-hybrid.py
"""

import os
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path

ROOT = Path(__file__).parent
VECTORA = ROOT
SEP = os.pathsep

# Libs coletadas por inteiro (código, dados e binários), porque carregam plugins
# ou arquivos por descoberta em runtime que a análise estática não enxerga.
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
    # A análise estática do PyInstaller não enxerga essa descoberta.
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


#: Dados do backend que precisam existir no instalador; sem eles o app sobe e
#: falha depois (migrations, contratos, assets, configuração padrão).
_REQUIRED_BACKEND_DATA = (
    "backend/defaults.env",
    "backend/storage/migrations/sqlite/schema.sql",
)


def _assert_backend_data_bundled(dist_dir: Path) -> None:
    """Confere que os dados do backend foram coletados e que o `defaults.env`
    instalado é idêntico ao do build."""
    import hashlib

    bundled_root = next(iter(dist_dir.rglob("backend/defaults.env")), None)
    missing = [rel for rel in _REQUIRED_BACKEND_DATA if not any(dist_dir.rglob(rel))]
    if missing or bundled_root is None:
        listed = ", ".join(missing or ["backend/defaults.env"])
        sys.exit(
            f"FALHA: dados do backend ausentes em {dist_dir} após o PyInstaller: "
            f"{listed}. Confira `--collect-data backend`."
        )
    if not any(dist_dir.rglob("backend/assets")):
        sys.exit(f"FALHA: backend/assets não apareceu em {dist_dir}.")
    source = VECTORA / "backend" / "defaults.env"
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()  # noqa: E731
    if digest(source) != digest(bundled_root):
        sys.exit(
            "FALHA: o defaults.env instalado difere do backend/defaults.env do "
            "build; a coleta do PyInstaller não copiou o arquivo certo."
        )
    print("Dados do backend embutidos (defaults.env idêntico ao do build).")


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


# Padrões de arquivo que nunca podem existir dentro de backend/ na hora do
# build: o PyInstaller coleta TUDO que está no pacote (`--collect-data backend`)
# para dentro do instalador, em claro, entregue a QUALQUER usuário que baixar
# o instalador. Um `.env`/chave/credencial esquecido ali vira público. A única
# exceção é backend/defaults.env (só valores padrão, sem segredo; ver
# `_assert_defaults_env_without_secrets`); segredos pessoais ficam num .env
# fora do pacote, que o PyInstaller nunca varre.
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
            "FALHA: arquivo de segredo dentro de backend/ — o PyInstaller "
            "copia isso, legível, para o instalador entregue a todo mundo que "
            f"o baixar:\n{listed}\n"
            "Mova pra .env (fora do pacote) ou ~/.vectora/.env antes "
            "de rodar o build."
        )


_SECRET_NAME_MARKS = ("SECRET", "TOKEN", "PRIVATE_KEY", "PASSWORD", "API_KEY")


def _assert_defaults_env_without_secrets() -> None:
    """`backend/defaults.env` vai para o instalador sem proteção: nenhuma
    variável de segredo pode carregar valor ali (o valor nunca é impresso)."""
    path = VECTORA / "backend" / "defaults.env"
    if not path.is_file():
        return
    offenders: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if value.strip() and any(mark in name.upper() for mark in _SECRET_NAME_MARKS):
            offenders.append(name)
    if offenders:
        sys.exit(
            "FALHA: backend/defaults.env traz valor em variável de segredo "
            f"({', '.join(offenders)}); ele vai legível para o instalador. "
            "Deixe vazio e defina por ambiente."
        )


def main() -> None:
    _assert_no_secrets_inside_backend()
    _assert_defaults_env_without_secrets()

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
    # O backend vai como código Python (bytecode), não compilado: coleta todos
    # os submódulos (inclusive os carregados por importlib e os específicos de
    # plataforma, como backend.services.ipc_pipe_win) e todos os arquivos de
    # dados (contratos JSON, migrations SQL, assets e defaults.env).
    collect += ["--collect-submodules", "backend", "--collect-data", "backend"]
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

    # PyInstaller: launcher + pacote backend + libs -> dist/vectora/
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
            # (delta real de poucos MB, não 1.5GB). O backend e as libs ficam como
            # bytecode Python.
            "--onedir",
            "--noconfirm",
            "--name=vectora",
            f"--distpath={ROOT / 'dist'}",
            f"--workpath={ROOT / 'build' / 'pyinstaller'}",
            f"--specpath={ROOT / 'build'}",
            "--add-binary",
            f"{nats_binary}{SEP}nats",
            "--add-binary",
            f"{ffmpeg_binary}{SEP}ffmpeg",
            "--add-binary",
            f"{ffprobe_binary}{SEP}ffmpeg",
            *speech_args,
            "--add-data",
            f"{VECTORA / 'frontend' / 'dist'}{SEP}chat_static",
            *collect,
            *hidden,
            *excludes,
            str(VECTORA / "launcher.py"),
        ],
        cwd=VECTORA,
        desc="PyInstaller -> dist/vectora",
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
    _assert_backend_data_bundled(dist_dir)
    _add_installed_test_runtime(dist_dir)
    total_mb = (
        sum(f.stat().st_size for f in dist_dir.rglob("*") if f.is_file()) / 1048576
    )
    print(f"\nBUILD OK — {dist_dir}  ({total_mb:.1f} MB na pasta)")


if __name__ == "__main__":
    main()

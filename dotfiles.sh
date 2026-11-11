#!/usr/bin/env bash
# Bootstrap reproduzível do ambiente de desenvolvimento Linux do Vectora.
# Escopo deliberadamente limitado ao projeto: não instala dotfiles nem apps pessoais.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
MODE=install
case "${1:-}" in
  "") ;;
  --check) MODE=check ;;
  --toolchain-only) MODE=toolchain ;;
  -h|--help)
    printf '%s\n' 'Uso: ./dotfiles.sh [--check|--toolchain-only]' \
      '  padrão             prepara a toolchain Linux (opcional)' \
      '  --check            apenas lista o estado das dependências' \
      '  --toolchain-only   prepara apenas a toolchain (equivalente ao padrão)'
    exit 0
    ;;
  *) printf 'Opção desconhecida: %s\n' "$1" >&2; exit 2 ;;
esac

OS=$(uname -s)
if [[ "$OS" != Linux ]]; then
  printf 'dotfiles.sh só prepara Linux (sistema detectado: %s).\n' "$OS" >&2
  exit 1
fi
if [[ ! -r /etc/os-release ]]; then
  printf 'Não foi possível identificar a distribuição: /etc/os-release ausente.\n' >&2
  exit 1
fi
# shellcheck disable=SC1091
. /etc/os-release
DISTRO=${ID:-unknown}
if [[ -e /run/ostree-booted ]]; then
  printf 'Distribuições imutáveis baseadas em ostree não são alteradas automaticamente.\n' >&2
  printf 'Instale as dependências do projeto no toolbox/container e rode scons install.\n' >&2
  exit 1
fi
if (( EUID == 0 )); then
  printf 'Execute como usuário normal; o script solicita sudo/doas quando necessário.\n' >&2
  exit 1
fi

case "$DISTRO" in
  ubuntu|pop|debian) PACKAGE_MANAGER=apt ;;
  fedora|fedora-asahi-remix) PACKAGE_MANAGER=dnf ;;
  arch|omarchy) PACKAGE_MANAGER=pacman ;;
  alpine) PACKAGE_MANAGER=apk ;;
  lfs|lebasix) PACKAGE_MANAGER=linuxbrew ;;
  *)
    printf 'Distribuição não mapeada (%s). Mapeamentos seguem config/install-platforms.list do dotfiles.\n' "$DISTRO" >&2
    exit 1
    ;;
esac

export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
export BUN_INSTALL="${BUN_INSTALL:-$HOME/.bun}"
export PNPM_HOME="${PNPM_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/pnpm}"
export GOPATH="${GOPATH:-$HOME/go}"
export PATH="$HOME/.local/bin:$HOME/.pyenv/bin:$HOME/.pyenv/shims:$BUN_INSTALL/bin:$PNPM_HOME:$HOME/.cargo/bin:$GOPATH/bin:$PATH"
for node_bin in "$NVM_DIR"/versions/node/v26*/bin; do
  [[ -d "$node_bin" ]] && { PATH="$node_bin:$PATH"; break; }
done
export PATH

command_exists() { command -v "$1" >/dev/null 2>&1; }
has_pyenv() { command_exists pyenv || [[ -x "$HOME/.pyenv/bin/pyenv" ]]; }
has_uv_tool() { uv tool list 2>/dev/null | awk '{print $1}' | grep -Fxq "$1"; }

printf 'Vectora Linux bootstrap — distro=%s (%s), modo=%s\n' "$DISTRO" "$PACKAGE_MANAGER" "$MODE"
printf '\nFerramentas detectadas:\n'
PENDING=0
for tool in git curl gcc make node pnpm bun pyenv python uv scons go rustup gitleaks prettier claude omniroute wrangler vercel; do
  if command_exists "$tool"; then printf '  [ok]      %s: %s\n' "$tool" "$(command -v "$tool")"
  else printf '  [pendente] %s\n' "$tool"; ((PENDING+=1))
  fi
done
if [[ -s "$NVM_DIR/nvm.sh" ]]; then printf '  [ok]      nvm: %s\n' "$NVM_DIR/nvm.sh"
else printf '  [pendente] nvm (gerenciador de Node.js)\n'; ((PENDING+=1)); fi
NODE_VERSION=$(node --version 2>/dev/null || true)
if [[ "$NODE_VERSION" == v26.* ]]; then printf '  [ok]      Node requerido pelo projeto: %s\n' "$NODE_VERSION"
else printf '  [pendente] Node.js 26 (detectado: %s)\n' "${NODE_VERSION:-ausente}"; ((PENDING+=1)); fi
EXPECTED_PNPM=$(sed -n 's/.*"packageManager": "pnpm@\([^"]*\)".*/\1/p' "$ROOT_DIR/frontend/package.json")
PNPM_VERSION=$(pnpm --version 2>/dev/null || true)
if [[ "$PNPM_VERSION" == "$EXPECTED_PNPM" ]]; then printf '  [ok]      pnpm requerido pelo workspace: %s\n' "$PNPM_VERSION"
else printf '  [pendente] pnpm %s (detectado: %s)\n' "$EXPECTED_PNPM" "${PNPM_VERSION:-ausente}"; ((PENDING+=1)); fi
printf '  Python do projeto: %s\n' "$(cat "$ROOT_DIR/.python-version")"
if has_pyenv && pyenv versions --bare | grep -Fxq "$(cat "$ROOT_DIR/.python-version")"; then
  printf '  [ok]      Python via pyenv: %s\n' "$(cat "$ROOT_DIR/.python-version")"
else printf '  [pendente] Python %s via pyenv\n' "$(cat "$ROOT_DIR/.python-version")"; ((PENDING+=1)); fi
for tool in pre-commit pyright mypy ruff ty deptry pip-check-reqs scons tavily-cli; do
  if has_uv_tool "$tool"; then printf '  [ok]      uv tool: %s\n' "$tool"
  else printf '  [pendente] uv tool: %s\n' "$tool"; ((PENDING+=1))
  fi
done
for artifact in \
  "$ROOT_DIR/frontend/node_modules/electron/path.txt" \
  "$ROOT_DIR/resources/nats-server" \
  "$ROOT_DIR/resources/ffmpeg" \
  "$ROOT_DIR/resources/ffprobe"; do
  if [[ -s "$artifact" ]]; then printf '  [ok]      %s\n' "${artifact#"$ROOT_DIR"/}"
  else printf '  [pendente] %s\n' "${artifact#"$ROOT_DIR"/}"; ((PENDING+=1)); fi
done
if [[ "$MODE" == check ]]; then
  if ((PENDING)); then printf '\n%d dependência(s) pendente(s).\n' "$PENDING"; exit 1; fi
  printf '\nToolchain e artefatos instalados.\n'
  exit 0
fi

if command_exists sudo; then SUDO=(sudo)
elif command_exists doas; then SUDO=(doas)
else printf 'Instale sudo ou doas para instalar dependências de sistema.\n' >&2; exit 1
fi
run_privileged() { "${SUDO[@]}" "$@"; }

install_packages() {
  local packages=() package
  case "$PACKAGE_MANAGER" in
    apt)
      local -a wanted=(build-essential curl ca-certificates git python3 pkg-config cmake \
        libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev libncursesw5-dev \
        xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev libgdbm-dev \
        libnss3-dev libpq-dev libatomic1 libayatana-appindicator3-dev)
      for package in "${wanted[@]}"; do dpkg-query -W -f='${db:Status-Abbrev}' "$package" 2>/dev/null | grep -q '^ii' || packages+=("$package"); done
      if ((${#packages[@]})); then run_privileged apt-get update; run_privileged env DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"; fi
      ;;
    dnf)
      local -a wanted=(gcc gcc-c++ make curl git python3 pkgconf-pkg-config cmake openssl-devel \
        zlib-devel bzip2-devel readline-devel sqlite-devel ncurses-devel xz-devel tk-devel \
        libffi-devel libxml2-devel libxslt-devel gdbm-devel libatomic libpq-devel)
      for package in "${wanted[@]}"; do rpm -q "$package" >/dev/null 2>&1 || packages+=("$package"); done
      if ((${#packages[@]})); then run_privileged dnf install -y "${packages[@]}"; fi
      ;;
    pacman)
      local -a wanted=(base-devel curl ca-certificates git python pkgconf cmake openssl zlib bzip2 \
        readline sqlite ncurses xz tk libxml2 libffi gdbm postgresql-libs)
      for package in "${wanted[@]}"; do pacman -Q "$package" >/dev/null 2>&1 || packages+=("$package"); done
      if ((${#packages[@]})); then run_privileged pacman -S --needed "${packages[@]}"; fi
      ;;
    apk)
      local -a wanted=(bash build-base curl ca-certificates git python3 pkgconf cmake linux-headers \
        openssl-dev zlib-dev bzip2-dev readline-dev sqlite-dev ncurses-dev xz-dev tk-dev \
        libxml2-dev libffi-dev gdbm-dev postgresql-dev cargo)
      for package in "${wanted[@]}"; do apk info -e "$package" >/dev/null 2>&1 || packages+=("$package"); done
      if ((${#packages[@]})); then run_privileged apk add "${packages[@]}"; fi
      ;;
    linuxbrew)
      if ! command_exists brew; then
        NONINTERACTIVE=1 /bin/bash -c "\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        [[ -x /home/linuxbrew/.linuxbrew/bin/brew ]] || { printf 'A instalação do Linuxbrew não produziu o executável esperado.\n' >&2; return 1; }
        eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
      fi
      local -a wanted=(git curl pkgconf cmake openssl readline sqlite xz tcl-tk)
      for package in "${wanted[@]}"; do brew list --versions "$package" >/dev/null 2>&1 || packages+=("$package"); done
      if ((${#packages[@]})); then brew install "${packages[@]}"; fi
      ;;
  esac
}

if [[ "$MODE" != check ]]; then
  install_packages

  if [[ ! -s "$NVM_DIR/nvm.sh" ]]; then
    mkdir -p "$NVM_DIR"
    curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
  fi
  # nvm modifies shell state and assumes nounset is disabled while sourced.
  set +u
  # shellcheck disable=SC1090
  . "$NVM_DIR/nvm.sh"
  NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || printf 0)
  if [[ "$NODE_MAJOR" != 26 ]]; then nvm install 26; fi
  nvm use 26 >/dev/null
  set -u

  if [[ ! -x "$BUN_INSTALL/bin/bun" ]]; then curl -fsSL https://bun.com/install | bash; fi
  export PATH="$BUN_INSTALL/bin:$PNPM_HOME:$PATH"
  PNPM_VERSION=$(node -p 'require(process.argv[1]).packageManager.split("@")[1]' "$ROOT_DIR/frontend/package.json")
  if ! command_exists pnpm || [[ "$(pnpm --version 2>/dev/null || true)" != "$PNPM_VERSION" ]]; then
    bun add --global "pnpm@$PNPM_VERSION"
  fi

  if ! has_pyenv; then
    git clone https://github.com/pyenv/pyenv.git "$HOME/.pyenv"
  fi
  export PATH="$HOME/.pyenv/bin:$HOME/.pyenv/shims:$PATH"
  eval "$(pyenv init - bash)"
  PYTHON_VERSION=$(cat "$ROOT_DIR/.python-version")
  if ! pyenv versions --bare | grep -Fxq "$PYTHON_VERSION"; then pyenv install "$PYTHON_VERSION"; fi
  export PYENV_VERSION="$PYTHON_VERSION"

  if ! command_exists uv; then curl -LsSf https://astral.sh/uv/install.sh | sh; fi
  export PATH="$HOME/.local/bin:$PATH"
  uv python install "$PYTHON_VERSION"
  uv tool install --upgrade pre-commit
  uv tool install --upgrade pyright
  uv tool install --upgrade mypy
  uv tool install --upgrade ruff
  uv tool install --upgrade ty
  uv tool install --upgrade deptry
  uv tool install --upgrade pip-check-reqs
  uv tool install --upgrade scons
  uv tool install --upgrade tavily-cli

  if ! command_exists go; then
    case "$(uname -m)" in x86_64) go_arch=amd64 ;; aarch64|arm64) go_arch=arm64 ;; *) printf 'Arquitetura sem bootstrap Go: %s\n' "$(uname -m)" >&2; exit 1 ;; esac
    go_json=$(curl -fsSL https://go.dev/dl/?mode=json)
    read -r go_file go_sha < <(GO_JSON="$go_json" GO_ARCH="$go_arch" python3 -c 'import json,os; x=next(x for r in json.loads(os.environ["GO_JSON"]) if r["stable"] for x in r["files"] if x["os"]=="linux" and x["arch"]==os.environ["GO_ARCH"] and x["kind"]=="archive"); print(x["filename"],x["sha256"])')
    temp=$(mktemp -d); trap 'rm -rf "$temp"' EXIT
    curl -fsSL "https://go.dev/dl/$go_file" -o "$temp/go.tgz"
    echo "$go_sha  $temp/go.tgz" | sha256sum -c -
    mkdir -p "$HOME/.local/share"
    tar -xzf "$temp/go.tgz" -C "$temp"
    [[ ! -e "$HOME/.local/share/go" ]] || { printf 'Recusando sobrescrever ~/.local/share/go\n' >&2; exit 1; }
    mv "$temp/go" "$HOME/.local/share/go"
    trap - EXIT; rm -rf "$temp"
    export PATH="$HOME/.local/share/go/bin:$GOPATH/bin:$PATH"
  fi
  export PATH="$HOME/.local/share/go/bin:$GOPATH/bin:$PATH"
  if ! command_exists rustup; then
    curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs | sh -s -- -y --profile default --default-toolchain stable --no-modify-path
  fi
  export PATH="$HOME/.cargo/bin:$PATH"
  rustup toolchain install stable --profile default
  rustup component add rustfmt clippy rust-analyzer rust-src --toolchain stable
  mkdir -p "$HOME/.local/bin"
  if ! command_exists gitleaks; then GOBIN="$HOME/.local/bin" go install github.com/zricethezav/gitleaks/v8@latest; fi

  for tool in prettier @anthropic-ai/claude-code omniroute wrangler vercel; do
    case "$tool" in
      prettier) executable=prettier ;;
      @anthropic-ai/claude-code) executable=claude ;;
      omniroute) executable=omniroute ;;
      wrangler) executable=wrangler ;;
      vercel) executable=vercel ;;
    esac
    if ! command_exists "$executable"; then bun add --global "$tool"; fi
  done
fi

printf '\nToolchain preparada. Para instalar as dependências do projeto, rode scons install.\n'

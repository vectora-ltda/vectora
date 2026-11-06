#!/bin/bash
# electron-builder deb: afterInstall. Um script próprio substitui o padrão do
# electron-builder, então este arquivo repete o que o padrão faz (link do
# comando, permissões do sandbox e perfil AppArmor) e acrescenta a atualização
# dos bancos de desktop entries e mime para o ícone e o launcher aparecerem sem
# logout/login.
set -e

APP_DIR=/opt/Vectora
APP_BIN="$APP_DIR/vectora"
COMMAND_LINK=/usr/bin/vectora

# Comando `vectora` no PATH. Com update-alternatives o link sobrevive a
# reinstalações e é removido de forma limpa no afterRemove.
if type update-alternatives >/dev/null 2>&1; then
  # Link antigo que não usa update-alternatives bloqueia o novo registro.
  if [ -L "$COMMAND_LINK" ] && [ -e "$COMMAND_LINK" ] \
    && [ "$(readlink "$COMMAND_LINK")" != "/etc/alternatives/vectora" ]; then
    rm -f "$COMMAND_LINK"
  fi
  update-alternatives --install "$COMMAND_LINK" vectora "$APP_BIN" 100 \
    || ln -sf "$APP_BIN" "$COMMAND_LINK"
else
  ln -sf "$APP_BIN" "$COMMAND_LINK"
fi

# O chrome-sandbox só precisa de SUID em kernels sem user namespaces.
if ! { [[ -L /proc/self/ns/user ]] && unshare --user true; }; then
  chmod 4755 "$APP_DIR/chrome-sandbox" || true
else
  chmod 0755 "$APP_DIR/chrome-sandbox" || true
fi

if hash update-mime-database 2>/dev/null; then
  update-mime-database /usr/share/mime || true
fi

if hash update-desktop-database 2>/dev/null; then
  update-desktop-database /usr/share/applications || true
fi

# Perfil AppArmor (Ubuntu 24+). Só instala quando o pacote o traz e a versão
# do AppArmor em execução sabe carregá-lo; no Ubuntu 22.04 o app roda sem ele.
if apparmor_status --enabled >/dev/null 2>&1; then
  PROFILE_SOURCE="$APP_DIR/resources/apparmor-profile"
  PROFILE_TARGET=/etc/apparmor.d/vectora
  if [ -f "$PROFILE_SOURCE" ]; then
    if apparmor_parser --skip-kernel-load --debug "$PROFILE_SOURCE" >/dev/null 2>&1; then
      cp -f "$PROFILE_SOURCE" "$PROFILE_TARGET"
      # Recarregar o perfil não faz sentido em chroot (imagens de cliente).
      if ! { [ -x /usr/bin/ischroot ] && /usr/bin/ischroot; } \
        && hash apparmor_parser 2>/dev/null; then
        apparmor_parser --replace --write-cache --skip-read-cache "$PROFILE_TARGET" || true
      fi
    else
      echo "Perfil AppArmor ignorado: esta versão do AppArmor não suporta o perfil empacotado."
    fi
  fi
fi

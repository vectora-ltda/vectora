#!/bin/bash
# electron-builder deb: afterRemove, contraparte de linux-after-install.sh.
# Remove o comando `vectora` e o perfil AppArmor e refaz o refresh dos bancos
# de desktop entries e mime depois da desinstalação.
set -e

APP_BIN=/opt/Vectora/vectora

if type update-alternatives >/dev/null 2>&1; then
  update-alternatives --remove vectora "$APP_BIN" || true
else
  rm -f /usr/bin/vectora
fi

PROFILE_TARGET=/etc/apparmor.d/vectora
if [ -f "$PROFILE_TARGET" ]; then
  # Descarrega o perfil do kernel antes de apagar o arquivo, para a política
  # não continuar ativa até o próximo boot. Em chroot isso não faz sentido.
  if apparmor_status --enabled >/dev/null 2>&1; then
    if ! { [ -x /usr/bin/ischroot ] && /usr/bin/ischroot; } \
      && hash apparmor_parser 2>/dev/null; then
      apparmor_parser --remove "$PROFILE_TARGET" || true
    fi
  fi
  rm -f "$PROFILE_TARGET"
fi

if hash update-desktop-database 2>/dev/null; then
  update-desktop-database /usr/share/applications || true
fi

if hash update-mime-database 2>/dev/null; then
  update-mime-database /usr/share/mime || true
fi

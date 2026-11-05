#!/bin/bash
# electron-builder deb: afterInstall — atualiza os bancos de dados de
# desktop entries/mime pra o ícone e o launcher aparecerem sem precisar
# de logout/login.
set -e

if hash update-desktop-database 2>/dev/null; then
  update-desktop-database /usr/share/applications || true
fi

if hash update-mime-database 2>/dev/null; then
  update-mime-database /usr/share/mime || true
fi

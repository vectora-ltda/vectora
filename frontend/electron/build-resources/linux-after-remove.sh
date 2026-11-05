#!/bin/bash
# electron-builder deb: afterRemove — contraparte de linux-after-install.sh,
# refaz o refresh dos bancos de desktop entries/mime pós-desinstalação.
set -e

if hash update-desktop-database 2>/dev/null; then
  update-desktop-database /usr/share/applications || true
fi

if hash update-mime-database 2>/dev/null; then
  update-mime-database /usr/share/mime || true
fi

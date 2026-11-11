#!/usr/bin/env bash
set -euo pipefail

for attempt in 1 2 3; do
  if sudo snap install snapcraft --classic; then
    exit 0
  fi
  if [ "$attempt" -lt 3 ]; then
    delay=$((attempt * 10))
    echo "Snap Store indisponível; nova tentativa em ${delay}s (${attempt}/3)."
    sleep "$delay"
  fi
done

echo "::error::Não foi possível instalar o Snapcraft após 3 tentativas."
exit 1

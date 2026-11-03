#!/usr/bin/env bash
set -euo pipefail

output_dir="${1:-./vectora-release-key}"
mkdir -p "$output_dir"
chmod 700 "$output_dir"
temporary_home="$(mktemp -d)"
trap 'rm -rf "$temporary_home"' EXIT
export GNUPGHOME="$temporary_home"

read -r -p "Release key name: " key_name
read -r -p "Release key email: " key_email
if [[ -z "$key_name" || -z "$key_email" ]]; then
  echo "A non-empty name and email are required." >&2
  exit 2
fi

gpg --quick-generate-key "$key_name <$key_email>" ed25519 sign 2y
fingerprint="$(gpg --batch --with-colons --list-secret-keys | awk -F: '$1 == "fpr" { print $10; exit }')"
gpg --batch --armor --export "$fingerprint" > "$output_dir/vectora-release-key.asc"
gpg --armor --export-secret-keys "$fingerprint" > "$output_dir/private-key.asc"
chmod 600 "$output_dir/private-key.asc"
echo "Created a two-year signing key in $output_dir. Store private-key.asc as a protected CI secret and publish only vectora-release-key.asc."

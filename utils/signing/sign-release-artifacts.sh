#!/usr/bin/env bash
set -euo pipefail

artifact_dir="${1:?usage: sign-release-artifacts.sh DIRECTORY}"
if [[ ! -d "$artifact_dir" ]]; then
  echo "Artifact directory does not exist: $artifact_dir" >&2
  exit 2
fi

pushd "$artifact_dir" >/dev/null
mapfile -d '' files < <(
  find . -type f ! -name 'SHA256SUMS*' ! -name '*.asc' -print0 | sort -z
)
if ((${#files[@]} == 0)); then
  echo "No release artifacts found in $artifact_dir" >&2
  exit 1
fi
sha256sum -- "${files[@]}" > SHA256SUMS

if [[ -z "${GPG_PRIVATE_KEY:-}" ]]; then
  echo "GPG_PRIVATE_KEY is not configured; publishing SHA256SUMS without detached signatures."
  popd >/dev/null
  exit 0
fi

temporary_home="$(mktemp -d)"
key_file="$(mktemp)"
passphrase_file="$(mktemp)"
cleanup() {
  rm -rf "$temporary_home"
  rm -f "$key_file" "$passphrase_file"
}
trap cleanup EXIT
chmod 700 "$temporary_home"
chmod 600 "$key_file" "$passphrase_file"
printf '%s' "$GPG_PRIVATE_KEY" > "$key_file"
printf '%s' "${GPG_PASSPHRASE:-}" > "$passphrase_file"
export GNUPGHOME="$temporary_home"
gpg --batch --import "$key_file"
fingerprint="$(gpg --batch --with-colons --list-secret-keys | awk -F: '$1 == "fpr" { print $10; exit }')"
if [[ -z "$fingerprint" ]]; then
  echo "The configured GPG private key has no secret-key fingerprint." >&2
  exit 1
fi
gpg --batch --armor --export "$fingerprint" > vectora-release-key.asc
for artifact in "${files[@]}" SHA256SUMS; do
  gpg --batch --yes --pinentry-mode loopback \
    --passphrase-file "$passphrase_file" --local-user "$fingerprint" \
    --armor --detach-sign "$artifact"
done
echo "Signed ${#files[@]} release artifact(s) and SHA256SUMS with GPG."
popd >/dev/null

#!/usr/bin/env bash
set -euo pipefail

image="${SNAPCRAFT_IMAGE:-ghcr.io/canonical/snapcraft:8_core22}"
compat_dir="${RUNNER_TEMP:?RUNNER_TEMP must be set}/snapcraft-compat"

docker pull "$image"
mkdir -p "$compat_dir"
cat > "$compat_dir/snapcraft" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

image="${SNAPCRAFT_IMAGE:-ghcr.io/canonical/snapcraft:8_core22}"
args=("$@")
if [[ "${args[0]:-}" == "snap" ]]; then
  args=(pack --destructive-mode "${args[@]:1}")
fi

exec docker run --rm -v "$PWD:/project" -w /project "$image" "${args[@]}"
EOF
chmod +x "$compat_dir/snapcraft"
echo "$compat_dir" >> "${GITHUB_PATH:?GITHUB_PATH must be set}"

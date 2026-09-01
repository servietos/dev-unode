#!/bin/bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export HOME=/data
export NPM_CONFIG_PREFIX=/data/npm
export PATH="/data/npm/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

mkdir -p /data/workspace /data/npm /data/.claude /data/bin

if [ -f /data/.env ]; then
  set -a
  # shellcheck disable=SC1091
  . /data/.env
  set +a
fi

MARKER=/data/.bootstrap-ok
if [ ! -f "$MARKER" ] || [ ! -x /data/bin/ttyd ] || [ ! -x /data/npm/bin/claude ]; then
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates curl git
  rm -rf /var/lib/apt/lists/*

  ARCH="$(uname -m)"
  case "$ARCH" in
    x86_64|amd64) TTYD_ASSET="ttyd.x86_64" ;;
    aarch64|arm64) TTYD_ASSET="ttyd.aarch64" ;;
    *) echo "Unsupported arch: $ARCH"; exit 1 ;;
  esac

  curl -fsSL "https://github.com/tsl0922/ttyd/releases/download/1.7.7/${TTYD_ASSET}" -o /data/bin/ttyd
  chmod +x /data/bin/ttyd

  npm install -g @anthropic-ai/claude-code
  touch "$MARKER"
fi

if [ ! -f /data/.env ]; then
  cat > /data/.env <<'EOF'
# Optional: Anthropic API key
# ANTHROPIC_API_KEY=sk-ant-...
EOF
fi

chown -R 1000:1000 /data || true

cd /data/workspace
exec /data/bin/ttyd --writable -p 7681 --interface 0.0.0.0 claude

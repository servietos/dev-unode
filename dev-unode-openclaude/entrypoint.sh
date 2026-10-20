#!/bin/bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export HOME=/data
export NPM_CONFIG_PREFIX=/data/npm
export PATH="/data/npm/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

mkdir -p /data/workspace /data/npm /data/bin

MARKER=/data/.openclaude-bootstrap-ok
if [ ! -f "$MARKER" ] || [ ! -x /data/bin/ttyd ] || [ ! -x /data/npm/bin/openclaude ]; then
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates curl git ripgrep
  rm -rf /var/lib/apt/lists/*

  ARCH="$(uname -m)"
  case "$ARCH" in
    x86_64|amd64) TTYD_ASSET="ttyd.x86_64" ;;
    aarch64|arm64) TTYD_ASSET="ttyd.aarch64" ;;
    *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
  esac

  curl -fsSL "https://github.com/tsl0922/ttyd/releases/download/1.7.7/${TTYD_ASSET}" -o /data/bin/ttyd
  chmod +x /data/bin/ttyd
  npm install -g @twigpine/openclaude@latest
  touch "$MARKER"
fi

cd /data/workspace
exec /data/bin/ttyd --writable -p 7681 --interface 0.0.0.0 /data/npm/bin/openclaude

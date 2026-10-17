#!/bin/sh
set -eu

apk add --no-cache ca-certificates curl tar
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64) ASSET_ARCH=x86_64 ;;
  aarch64|arm64) ASSET_ARCH=aarch64 ;;
  *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac
VERSION="${GOLDCOIN_VERSION:-0.15.0}"
ARCHIVE="goldcoin-${VERSION}-${ASSET_ARCH}-linux-gnu.tar.gz"
URL="https://github.com/goldcoin/goldcoin/releases/download/v${VERSION}/${ARCHIVE}"
mkdir -p /data/bin /data/.goldcoin
if [ ! -x "/data/bin/goldcoind" ]; then
  echo "Downloading Goldcoin Core ${VERSION} (${ASSET_ARCH})..."
  curl -fL "$URL" -o /tmp/goldcoin.tar.gz
  tar -xzf /tmp/goldcoin.tar.gz -C /tmp
  BIN_DIR="$(find /tmp -type f -name goldcoind -print -quit | xargs -r dirname)"
  [ -n "$BIN_DIR" ] || { echo "goldcoind not found in release archive" >&2; exit 1; }
  cp "$BIN_DIR/goldcoind" "$BIN_DIR/goldcoin-cli" /data/bin/
  chmod 755 /data/bin/goldcoind /data/bin/goldcoin-cli
fi
cat > /data/.goldcoin/goldcoin.conf <<EOF
server=1
daemon=0
listen=1
port=8333
rpcport=8332
rpcbind=0.0.0.0
rpcallowip=172.16.0.0/12
rpcuser=${RPC_USER}
rpcpassword=${RPC_PASSWORD}
EOF
exec /data/bin/goldcoind -datadir=/data/.goldcoin -conf=/data/.goldcoin/goldcoin.conf -printtoconsole

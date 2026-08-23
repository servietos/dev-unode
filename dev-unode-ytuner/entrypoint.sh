#!/bin/bash
set -e
# Konfig nach /data spiegeln, damit sie persistent bleibt
mkdir -p /data/config
if [ ! -f /data/config/ytuner.ini ] && [ -f /opt/ytuner/ytuner.ini ]; then
  cp /opt/ytuner/ytuner.ini /data/config/
fi
cd /data/config
exec ./ytuner 2>/dev/null || exec /opt/ytuner/ytuner

#!/bin/sh
set -e
# Persistente Ordner (per ytuner.ini auf /data zeigen)
mkdir -p /data/config /data/cache /data/db
if [ ! -f /data/config/ytuner.ini ]; then
  cp /app/ytuner.ini /data/config/
fi
cd /data/config
exec /app/ytuner

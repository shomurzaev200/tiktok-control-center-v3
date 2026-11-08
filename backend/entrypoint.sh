#!/bin/sh
set -e
mkdir -p /app/profiles /app/backups
chown appuser:appgroup /app/profiles /app/backups || true
exec runuser -u appuser -- "$@"

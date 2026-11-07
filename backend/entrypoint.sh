#!/bin/sh
set -e
mkdir -p /app/profiles /app/backups
chown -R appuser:appgroup /app/profiles /app/backups
exec runuser -u appuser -- "$@"

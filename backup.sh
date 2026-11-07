#!/usr/bin/env bash
# ==============================================================================
# TikTok Control Center v3.0 — Создание и верификация резервной копии PostgreSQL
# ==============================================================================
set -euo pipefail

mkdir -p backups
chmod 700 backups
source .env

TS=$(date -u +"%Y%m%d_%H%M%S")
BACKUP_FILE="backups/tt_v3_backup_${TS}.dump"

echo "💾 Создаём резервную копию БД ${POSTGRES_DB} -> ${BACKUP_FILE}..."
docker compose exec -T postgres pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -Fc > "${BACKUP_FILE}"

echo "🔍 Проверяем целостность созданного архива..."
docker compose exec -T postgres pg_restore --list "/backups/tt_v3_backup_${TS}.dump" >/dev/null
SIZE=$(wc -c < "${BACKUP_FILE}")
if [ "${SIZE}" -lt 100 ]; then
  echo "❌ Ошибка: размер резервной копии подозрительно мал (${SIZE} байт)."
  exit 1
fi
echo "✅ Резервная копия успешно проверена (${SIZE} байт)."

# Удаление старых копий старше BACKUP_RETENTION_DAYS
find backups/ -name "tt_v3_backup_*.dump" -mtime +"${BACKUP_RETENTION_DAYS:-14}" -delete || true

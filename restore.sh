#!/usr/bin/env bash
# ==============================================================================
# TikTok Control Center v3.0 — Восстановление PostgreSQL из резервной копии
# ==============================================================================
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Использование: ./restore.sh backups/<имя_файла.dump>"
  exit 1
fi

DUMP_PATH="$1"
if [ ! -f "${DUMP_PATH}" ]; then
  echo "❌ Файл ${DUMP_PATH} не найден."
  exit 1
fi

source .env
BASENAME=$(basename "${DUMP_PATH}")

echo "🔍 Проверка целостности архива перед восстановлением..."
docker compose exec -T postgres pg_restore --list "/backups/${BASENAME}" >/dev/null

echo "⏸ Останавливаем backend и worker перед восстановлением БД..."
docker compose stop backend worker telegram

echo "♻️ Восстанавливаем базу данных ${POSTGRES_DB} из ${BASENAME}..."
docker compose exec -T postgres pg_restore -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --clean --if-exists "/backups/${BASENAME}"

echo "▶️ Запускаем сервисы и применяем миграции..."
docker compose start backend worker telegram
docker compose exec -T backend alembic upgrade head || true
echo "✅ Восстановление успешно завершено!"

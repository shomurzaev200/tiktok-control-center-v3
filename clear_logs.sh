#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source .env
echo "Очищаю старые системные логи и снимки метрик в PostgreSQL..."
docker compose exec -T postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -c "TRUNCATE system_logs, health_history RESTART IDENTITY;"
echo "Готово. Логи контейнеров уже новые, если перед этим был docker compose down."

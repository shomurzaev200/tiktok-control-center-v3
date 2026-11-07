#!/usr/bin/env bash
# ==============================================================================
# TikTok Control Center v3.0 — Безопасное обновление с резервной копией и откатом
# ==============================================================================
set -euo pipefail

echo "📦 [1/5] Создание резервной копии перед обновлением и миграцией..."
./backup.sh
LAST_BACKUP=$(ls -t backups/tt_v3_backup_*.dump | head -n 1)
PREV_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "")

echo "🔄 [2/5] Получение обновлений из Git..."
if [ -d .git ] && git remote | grep -q origin; then
  git pull --ff-only origin main || true
fi

echo "🐳 [3/5] Пересборка образов и запуск контейнеров..."
docker compose build
docker compose up -d

echo "🗄 [4/5] Применение миграций Alembic..."
if ! docker compose exec -T backend alembic upgrade head; then
  echo "❌ Ошибка миграции! Выполняем автоматический откат..."
  [ -n "${PREV_COMMIT}" ] && git checkout "${PREV_COMMIT}" || true
  ./restore.sh "${LAST_BACKUP}"
  exit 1
fi

echo "🩺 [5/5] Проверка работоспособности после обновления..."
sleep 5
if ! docker compose exec -T backend curl -fsS http://localhost:8000/api/health/ready >/dev/null; then
  echo "❌ Healthcheck не пройден! Откатываемся на предыдущую версию и бэкап ${LAST_BACKUP}..."
  [ -n "${PREV_COMMIT}" ] && git checkout "${PREV_COMMIT}" || true
  docker compose up -d --build
  ./restore.sh "${LAST_BACKUP}"
  exit 1
fi

echo "✅ Обновление до новой версии успешно завершено и проверено!"

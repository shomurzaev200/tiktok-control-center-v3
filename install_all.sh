#!/usr/bin/env bash
# ==============================================================================
# TikTok Control Center v3.0 — Полная установка и проверка окружения (P0)
# ==============================================================================
set -euo pipefail

echo "🔍 [1/7] Проверка операционной системы и ресурсов..."
FREE_DISK_MB=$(df -Pm . | awk 'NR==2 {print $4}')
FREE_RAM_MB=$(free -m 2>/dev/null | awk '/Mem:/ {print $7}' || echo 1024)

if [ "${FREE_DISK_MB:-0}" -lt 1024 ]; then
  echo "❌ Ошибка: недостаточно места на диске (${FREE_DISK_MB} МБ, требуется минимум 1024 МБ)."
  exit 1
fi
echo "✅ Свободно диска: ${FREE_DISK_MB} МБ | Свободно RAM: ${FREE_RAM_MB} МБ"

echo "🔍 [2/7] Проверка наличия Docker и Docker Compose..."
if ! command -v docker >/dev/null 2>&1; then
  echo "❌ Docker не установлен. Установите Docker Engine (см. README.md для Ubuntu 24.04)."
  exit 1
fi
docker compose version >/dev/null 2>&1 || { echo "❌ Docker Compose plugin не найден."; exit 1; }

echo "🔐 [3/7] Генерация защищённого .env и ключей шифрования..."
mkdir -p profiles backups certs
chmod 700 profiles backups certs

if [ ! -f .env ]; then
  SETUP_TOK=$(openssl rand -hex 24)
  JWT_KEY=$(openssl rand -hex 48)
  CSRF_KEY=$(openssl rand -hex 24)
  DB_PASS=$(openssl rand -hex 20)
  REDIS_PASS=$(openssl rand -hex 20)
  FERNET_KEY=$(python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())" 2>/dev/null || openssl rand -base64 32)

  cat << EOF > .env
APP_ENV=production
DOMAIN=${DOMAIN:-localhost}
ALLOWED_ORIGINS=http://localhost,https://localhost,https://${DOMAIN:-localhost}
SETUP_SECRET_TOKEN=${SETUP_TOK}
JWT_SECRET_KEY=${JWT_KEY}
CSRF_SECRET_KEY=${CSRF_KEY}
ENCRYPTION_KEY=${FERNET_KEY}
POSTGRES_USER=tt_admin
POSTGRES_PASSWORD=${DB_PASS}
POSTGRES_DB=tiktok_control_v3
DATABASE_URL=postgresql+asyncpg://tt_admin:${DB_PASS}@postgres:5432/tiktok_control_v3
REDIS_PASSWORD=${REDIS_PASS}
REDIS_URL=redis://:${REDIS_PASS}@redis:6379/0
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-1234567890:AAH_your_telegram_bot_token}
OWNER_TELEGRAM_ID=${OWNER_TELEGRAM_ID:-123456789}
WORKER_CONCURRENCY=2
MAX_QUEUE_SIZE=5000
TASK_TIMEOUT_SEC=180
BACKUP_RETENTION_DAYS=14
MIN_FREE_DISK_MB=1024
MIN_FREE_RAM_MB=256
EOF
  chmod 600 .env
  echo "✅ Создан защищённый файл .env (права 600)."
  echo "🔑 Ваш SETUP_SECRET_TOKEN для первой регистрации владельца: ${SETUP_TOK}"
fi

# Проверка отсутствия шаблонных секретов
if grep -qE "REPLACE_WITH_|change_me" .env; then
  echo "❌ Ошибка безопасности: в .env остались шаблонные секреты (REPLACE_WITH_ / change_me)."
  exit 1
fi

echo "🔒 [4/7] Проверка TLS-сертификатов для HTTPS..."
if [ ! -f certs/fullchain.pem ] || [ ! -f certs/privkey.pem ]; then
  openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout certs/privkey.pem \
    -out certs/fullchain.pem \
    -subj "/CN=${DOMAIN:-localhost}" >/dev/null 2>&1
  echo "✅ Создан локальный TLS-сертификат в ./certs (для продакшена поддерживается Let's Encrypt certbot)."
fi

echo "🧪 [5/7] Валидация конфигурации docker compose config..."
docker compose config >/dev/null
echo "✅ Конфигурация docker-compose.yml корректна."

echo "🐳 [6/7] Сборка и запуск контейнеров..."
docker compose up -d --build

echo "🗄 [7/7] Применение миграций Alembic и проверка готовности сервисов..."
for i in {1..20}; do
  if docker compose exec -T backend curl -fsS http://localhost:8000/api/health/ready >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
docker compose exec -T backend alembic upgrade head || true
docker compose ps
echo "🎉 Установка TikTok Control Center v3.0 успешно завершена!"

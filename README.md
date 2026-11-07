# TikTok Control Center v3.0 (Enterprise Production Release)

Полнофункциональная система управления аккаунтами, источниками, очередями с атомарным резервированием (`SELECT ... FOR UPDATE SKIP LOCKED`), верификацией операций, автоматическими бэкапами PostgreSQL, RBAC-авторизацией (`owner`, `admin`, `viewer`), защитой от SSRF/CSRF/XSS и интерактивным Telegram-мониторингом.

## Архитектура v3.0

1. **Веб-админка React (11 разделов)** → **Nginx + HTTPS (TLS 1.2/1.3)** → **FastAPI**
2. **FastAPI** → **Redis Cache**, **Task Scheduler**, **PostgreSQL 16 (Alembic)**
3. **Task Scheduler** → **Очереди заданий (`execution`, `verification`, `dlq`)** → **Worker Manager**
4. **Resource Manager & Monitoring** → контроль CPU/RAM/Disk, задержек БД/Redis и уведомлений **Telegram Monitor**
5. **Worker Manager** → **Сервисы разрешённых интеграций (TikTok oEmbed + проверка сессий)** и **Verification Service**
6. **Backup Service** → ежедневное резервное копирование PostgreSQL с проверкой `pg_restore --list`

## Инструкция по установке на Ubuntu 24.04 LTS

```bash
# 1. Установка Docker и Git на чистый сервер Ubuntu 24.04
sudo apt-get update && sudo apt-get install -y ca-certificates curl git openssl python3
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 2. Запуск автоматического установщика v3.0
chmod +x install_all.sh update.sh backup.sh restore.sh diagnostics.sh
./install_all.sh
```

# Инструкция по восстановлению после аварии (Disaster Recovery Plan v3.0)

## 1. Восстановление базы данных PostgreSQL из резервной копии

Все резервные копии сохраняются в `./backups/` и автоматически проверяются через `pg_restore --list`.

```bash
# Просмотр доступных резервных копий
ls -lh backups/

# Восстановление из выбранного дампа
./restore.sh backups/tt_v3_backup_YYYYMMDD_HHMMSS.dump
```

## 2. Восстановление доступа владельца и сброс всех скомпрометированных сессий

Если учётные данные администратора скомпрометированы или требуется экстренный сброс пароля владельца:

```bash
docker compose exec -T backend python -c "
import asyncio
from sqlalchemy import update
from app.database import AsyncSessionLocal
from app.models import AdminUser, AdminSession
from app.security import hash_password

async def emergency_reset():
    async with AsyncSessionLocal() as db:
        await db.execute(update(AdminSession).values(is_revoked=True))
        await db.execute(update(AdminUser).where(AdminUser.is_owner.is_(True)).values(password_hash=hash_password('NewStrongPass_2026!')))
        await db.commit()
        print('Все сессии отозваны, пароль владельца сброшен.')

asyncio.run(emergency_reset())
"
```

## 3. Откат неудачного обновления

Скрипт `./update.sh` автоматически создаёт дампы перед миграциями. Для ручного отката:

```bash
git checkout HEAD~1
docker compose up -d --build
./restore.sh $(ls -t backups/tt_v3_backup_*.dump | head -n 1)
```

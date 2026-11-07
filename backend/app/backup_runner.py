import asyncio
import signal
from app.database import AsyncSessionLocal
from app.logger import log_event
from app.services.backup_service import backup_service

stop_event = asyncio.Event()


def _handle_stop(*_):
    stop_event.set()


async def main():
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    await log_event("backup", "INFO", "service_started", "Сервис автоматического резервного копирования запущен")

    while not stop_event.is_set():
        try:
            async with AsyncSessionLocal() as db:
                await backup_service.create_and_verify_backup(db, notes="daily_automated")
                await backup_service.cleanup_old_backups(db)
        except Exception as exc:
            await log_event("backup", "ERROR", "backup_cycle_err", str(exc), exc)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=86400)
        except asyncio.TimeoutError:
            pass


if __name__ == "__main__":
    asyncio.run(main())

import shutil
from datetime import datetime, timedelta, timezone
import psutil
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.database import redis_client, measure_db_latency_ms, measure_redis_latency_ms
from app.models import HealthHistory, SystemLog, AdminSession, NotificationLog
from app.services.queue_service import enqueue_notification


class MonitoringService:
    """
    Мониторинг ресурсов сервера (CPU, RAM, Disk), состояния контейнеров/сервисов,
    задержек PostgreSQL и Redis, пороговых аварийных уведомлений и очистки старых данных.
    """

    async def collect_system_metrics(self) -> dict:
        cpu_pct = round(psutil.cpu_percent(interval=0.05), 1)
        vm = psutil.virtual_memory()
        ram_pct = round(vm.percent, 1)
        free_ram_mb = int(vm.available // (1024 * 1024))

        disk = shutil.disk_usage("/")
        free_disk_mb = int(disk.free // (1024 * 1024))
        disk_pct = round((disk.used / disk.total) * 100, 1) if disk.total else 0.0

        db_ms = await measure_db_latency_ms()
        redis_ms = await measure_redis_latency_ms()

        worker_hb = None
        tg_hb = None
        try:
            worker_hb = await redis_client.get("worker:heartbeat")
            tg_hb = await redis_client.get("telegram:heartbeat")
        except Exception:
            pass

        return {
            "cpu_percent": cpu_pct,
            "ram_percent": ram_pct,
            "free_ram_mb": free_ram_mb,
            "disk_percent": disk_pct,
            "free_disk_mb": free_disk_mb,
            "db_latency_ms": db_ms,
            "redis_latency_ms": redis_ms,
            "db_online": db_ms >= 0,
            "redis_online": redis_ms >= 0,
            "worker_online": bool(worker_hb),
            "telegram_online": bool(tg_hb),
            "resources_healthy": (
                free_disk_mb >= settings.MIN_FREE_DISK_MB and free_ram_mb >= settings.MIN_FREE_RAM_MB
            ),
        }

    async def record_health_snapshot(self, db: AsyncSession) -> dict:
        metrics = await self.collect_system_metrics()
        snap = HealthHistory(
            cpu_percent=metrics["cpu_percent"],
            ram_percent=metrics["ram_percent"],
            free_ram_mb=metrics["free_ram_mb"],
            free_disk_mb=metrics["free_disk_mb"],
            db_latency_ms=metrics["db_latency_ms"],
            redis_latency_ms=metrics["redis_latency_ms"],
            worker_online=metrics["worker_online"],
            telegram_online=metrics["telegram_online"],
        )
        db.add(snap)
        await db.commit()

        # Проверка порогов и уведомления о нехватке места / памяти
        if metrics["free_disk_mb"] < settings.MIN_FREE_DISK_MB:
            await enqueue_notification(
                db,
                category="alert",
                title="⚠️ Критически мало места на диске",
                message=f"Свободно {metrics['free_disk_mb']} МБ (порог: {settings.MIN_FREE_DISK_MB} МБ)",
            )
        if metrics["free_ram_mb"] < settings.MIN_FREE_RAM_MB:
            await enqueue_notification(
                db,
                category="alert",
                title="⚠️ Низкий объём свободной памяти RAM",
                message=f"Доступно {metrics['free_ram_mb']} МБ (порог: {settings.MIN_FREE_RAM_MB} МБ)",
            )
        return metrics

    async def cleanup_stale_records(self, db: AsyncSession) -> dict:
        now = datetime.now(timezone.utc)
        log_cutoff = now - timedelta(days=14)
        hist_cutoff = now - timedelta(days=7)

        del_logs = await db.execute(delete(SystemLog).where(SystemLog.created_at < log_cutoff))
        del_hist = await db.execute(delete(HealthHistory).where(HealthHistory.created_at < hist_cutoff))
        await db.commit()
        return {
            "deleted_logs": del_logs.rowcount or 0,
            "deleted_health_history": del_hist.rowcount or 0,
            "deleted_expired_sessions": 0,
        }


monitoring_service = MonitoringService()

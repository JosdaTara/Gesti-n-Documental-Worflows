"""Tareas programadas (reemplaza el Schedule Trigger de n8n)."""
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from app.core.db import SessionLocal
from app.core.config import settings

log = logging.getLogger("motor")
_sched: BackgroundScheduler | None = None


def _job():
    from app.services.indexing_service import sweep_pending
    db = SessionLocal()
    try:
        sweep_pending(db, "cron")
    except Exception:
        log.exception("Fallo el barrido de indexación")
    finally:
        db.close()


def start():
    global _sched
    if _sched and _sched.running:
        return
    _sched = BackgroundScheduler(timezone="America/Bogota")
    _sched.add_job(_job, "interval", minutes=settings.INDEXER_CRON_MINUTES, id="indexer",
                   max_instances=1, coalesce=True)
    _sched.start()
    log.info("Scheduler iniciado: indexación cada %s min", settings.INDEXER_CRON_MINUTES)


def shutdown():
    global _sched
    if _sched and _sched.running:
        _sched.shutdown(wait=False)
    _sched = None

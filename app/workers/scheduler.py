from apscheduler.schedulers.background import BackgroundScheduler
from app.core.db import SessionLocal
from app.services.indexing_service import sweep_pending
from app.core.config import settings

_sched = None

def _job():
    db = SessionLocal()
    try:
        sweep_pending(db)
    finally:
        db.close()

def start():
    global _sched
    _sched = BackgroundScheduler()
    _sched.add_job(_job, "interval", minutes=settings.INDEXER_CRON_MINUTES, id="indexer")
    _sched.start()

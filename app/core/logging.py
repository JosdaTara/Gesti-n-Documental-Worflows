import time
from contextlib import contextmanager
from sqlalchemy.orm import Session
from app.models import Ejecucion

@contextmanager
def log_execution(db: Session, flujo: str, disparador: str = "manual", entrada=None):
    rec = Ejecucion(flujo=flujo, disparador=disparador, entrada=entrada)
    db.add(rec); db.commit(); db.refresh(rec)
    t0 = time.monotonic()
    try:
        yield rec
        rec.resultado = {"ok": True}
    except Exception as e:
        rec.error = str(e)
        rec.resultado = {"ok": False}
        raise
    finally:
        rec.fin = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        rec.duracion_ms = int((time.monotonic() - t0) * 1000)
        db.commit()

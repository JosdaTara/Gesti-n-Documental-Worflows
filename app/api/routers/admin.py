"""Administración del motor de automatización: disparo manual y consulta de ejecuciones."""
import hmac
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.db import get_db
from app.core.security import bearer, get_current_user, require_role
from app.models import Ejecucion

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _admin_o_cron(x_cron_token: str | None = Header(default=None),
                  creds: HTTPAuthorizationCredentials = Depends(bearer),
                  db: Session = Depends(get_db)) -> str:
    """Permite a un administrador o a un cron externo con `X-Cron-Token`."""
    if settings.CRON_SECRET and x_cron_token and hmac.compare_digest(x_cron_token, settings.CRON_SECRET):
        return "cron"
    u = get_current_user(creds, db)
    if u["rol"] != "admin":
        raise HTTPException(status_code=403, detail="No tiene permisos para esta acción")
    return "manual"


@router.post("/run-indexer")
def run_indexer(disparador: str = Depends(_admin_o_cron), db: Session = Depends(get_db)):
    from app.services.indexing_service import sweep_pending
    return {"ok": True, **sweep_pending(db, disparador)}


@router.get("/ejecuciones")
def ejecuciones(flujo: str | None = None,
                estado: str | None = Query(default=None, pattern="^(ok|error)$"),
                limit: int = Query(default=50, ge=1, le=500),
                db: Session = Depends(get_db), u=Depends(require_role("admin"))):
    q = db.query(Ejecucion)
    if flujo:
        q = q.filter(Ejecucion.flujo == flujo)
    if estado == "ok":
        q = q.filter(Ejecucion.error.is_(None))
    elif estado == "error":
        q = q.filter(Ejecucion.error.isnot(None))
    rows = q.order_by(Ejecucion.id.desc()).limit(limit).all()
    return {"ejecuciones": [
        {"id": e.id, "flujo": e.flujo, "disparador": e.disparador, "entrada": e.entrada,
         "resultado": e.resultado, "error": e.error,
         "inicio": e.inicio.isoformat() if e.inicio else None,
         "fin": e.fin.isoformat() if e.fin else None, "duracion_ms": e.duracion_ms}
        for e in rows]}

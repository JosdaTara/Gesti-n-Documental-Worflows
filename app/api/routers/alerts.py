"""Alertas que generan los flujos de automatización (p. ej. procesar_factura)."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.audit import auditar
from app.core.db import get_db
from app.core.security import require_permiso
from app.models import Alerta

router = APIRouter(prefix="/api/alertas", tags=["alertas"])


def alerta_out(a: Alerta) -> dict:
    return {"id": a.id, "tipo": a.tipo, "severidad": a.severidad, "mensaje": a.mensaje, "entidad": a.entidad,
            "entidad_id": a.entidad_id, "estado": a.estado,
            "creado_en": a.creado_en.isoformat() if a.creado_en else None,
            "atendida_en": a.atendida_en.isoformat() if a.atendida_en else None}


@router.get("")
def listar(estado: str | None = Query(default="abierta", pattern="^(abierta|atendida|todas)$"),
           limit: int = Query(default=100, ge=1, le=500),
           db: Session = Depends(get_db), u=Depends(require_permiso("ver_alertas"))):
    q = db.query(Alerta)
    if estado and estado != "todas":
        q = q.filter(Alerta.estado == estado)
    return {"alertas": [alerta_out(a) for a in q.order_by(Alerta.id.desc()).limit(limit).all()],
            "abiertas": db.query(Alerta).filter(Alerta.estado == "abierta").count()}


@router.post("/{alerta_id}/atender")
def atender(alerta_id: int, db: Session = Depends(get_db), u=Depends(require_permiso("atender_alertas"))):
    a = db.query(Alerta).filter(Alerta.id == alerta_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Alerta no encontrada")
    a.estado = "atendida"; a.atendida_por = u["id"]; a.atendida_en = datetime.now(timezone.utc)
    db.commit(); db.refresh(a)
    auditar("atender_alerta", usuario_id=u["id"], entidad="alerta", entidad_id=str(a.id), detalle={"tipo": a.tipo})
    return alerta_out(a)

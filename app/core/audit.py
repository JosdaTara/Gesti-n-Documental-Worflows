"""Registro de auditoría (acciones de la IA y acciones sensibles de los usuarios).

Usa su propia sesión de base de datos para que el registro se conserve aunque el flujo
que lo originó falle y haga rollback. Nunca lanza excepciones: si no se puede auditar,
se deja constancia en el log del servidor y el flujo continúa.
"""
import json
import logging

log = logging.getLogger("motor.auditoria")


def auditar(accion: str, *, usuario_id: int | None = None, entidad: str | None = None,
            entidad_id: str | None = None, detalle: dict | None = None, ip: str | None = None) -> None:
    from app.core.db import SessionLocal
    from app.models import Auditoria
    db = SessionLocal()
    try:
        det = json.loads(json.dumps(detalle, default=str, ensure_ascii=False)) if detalle else None
        db.add(Auditoria(usuario_id=usuario_id, accion=accion[:80], entidad=entidad,
                         entidad_id=str(entidad_id) if entidad_id else None, detalle=det, ip=ip))
        db.commit()
    except Exception:
        db.rollback()
        log.exception("No se pudo registrar la auditoría de %s", accion)
    finally:
        db.close()

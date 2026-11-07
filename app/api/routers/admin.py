"""Administración del motor de automatización: disparo manual y consulta de ejecuciones."""
import hmac
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.db import get_db
from app.core.audit import auditar
from app.core.security import ROLES, bearer, get_current_user, hash_password, require_permiso
from app.models import Auditoria, Ejecucion, Usuario
from app.schemas import UsuarioCrear, UsuarioEditar

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
                db: Session = Depends(get_db), u=Depends(require_permiso("ver_ejecuciones"))):
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


@router.post("/run-invoices")
def run_facturas(disparador: str = Depends(_admin_o_cron), db: Session = Depends(get_db)):
    """Reintenta las facturas aprobadas que aún no pasaron por procesar_factura."""
    from app.services.automatizacion_facturas import barrido_pendientes
    return {"ok": True, **barrido_pendientes(db, disparador)}


@router.get("/auditoria")
def auditoria(accion: str | None = None, usuario_id: int | None = None,
              limit: int = Query(default=100, ge=1, le=500),
              db: Session = Depends(get_db), u=Depends(require_permiso("ver_auditoria"))):
    q = db.query(Auditoria)
    if accion:
        q = q.filter(Auditoria.accion.like(f"{accion}%"))
    if usuario_id:
        q = q.filter(Auditoria.usuario_id == usuario_id)
    return {"auditoria": [
        {"id": a.id, "fecha": a.fecha.isoformat() if a.fecha else None, "usuario_id": a.usuario_id,
         "accion": a.accion, "entidad": a.entidad, "entidad_id": a.entidad_id, "detalle": a.detalle}
        for a in q.order_by(Auditoria.id.desc()).limit(limit).all()]}


# --- usuarios -----------------------------------------------------------------------

def _usuario_out(x: Usuario) -> dict:
    from app.core.security import normalizar_rol
    return {"id": x.id, "usuario": x.usuario, "rol": normalizar_rol(x.rol), "activo": x.activo is not False,
            "creado_en": x.creado_en.isoformat() if x.creado_en else None}


@router.get("/usuarios")
def listar_usuarios(db: Session = Depends(get_db), u=Depends(require_permiso("gestionar_usuarios"))):
    return {"usuarios": [_usuario_out(x) for x in db.query(Usuario).order_by(Usuario.usuario).all()], "roles": list(ROLES)}


@router.post("/usuarios")
def crear_usuario(body: UsuarioCrear, db: Session = Depends(get_db), u=Depends(require_permiso("gestionar_usuarios"))):
    if db.query(Usuario).filter(Usuario.usuario == body.usuario).first():
        raise HTTPException(status_code=400, detail="Ese nombre de usuario ya existe")
    x = Usuario(usuario=body.usuario, clave_hash=hash_password(body.clave), rol=body.rol, activo=True)
    db.add(x); db.commit(); db.refresh(x)
    auditar("crear_usuario", usuario_id=u["id"], entidad="usuario", entidad_id=str(x.id), detalle={"rol": x.rol})
    return _usuario_out(x)


@router.patch("/usuarios/{usuario_id}")
def editar_usuario(usuario_id: int, body: UsuarioEditar, db: Session = Depends(get_db),
                   u=Depends(require_permiso("gestionar_usuarios"))):
    x = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not x:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    cambios = body.model_dump(exclude_unset=True)
    if x.id == u["id"] and (cambios.get("rol", "admin") != "admin" or cambios.get("activo") is False):
        raise HTTPException(status_code=400, detail="No puede quitarse el rol de administrador ni desactivarse a sí mismo")
    if "rol" in cambios:
        x.rol = cambios["rol"]
    if "activo" in cambios:
        x.activo = cambios["activo"]
    if cambios.get("clave"):
        x.clave_hash = hash_password(cambios["clave"])
    db.commit(); db.refresh(x)
    auditar("editar_usuario", usuario_id=u["id"], entidad="usuario", entidad_id=str(x.id),
            detalle={k: v for k, v in cambios.items() if k != "clave"} | ({"clave": "cambiada"} if cambios.get("clave") else {}))
    return _usuario_out(x)

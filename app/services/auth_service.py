import logging
from datetime import datetime, timezone, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import log_execution
from app.core.security import create_token, hash_password, normalizar_rol, verify_password
from app.models import Sesion, Usuario

log = logging.getLogger("motor.auth")
CLAVE_DEV = "Cambiar-2026"   # solo para desarrollo local (APP_ENV=dev)


def es_produccion() -> bool:
    return settings.APP_ENV.lower() in ("prod", "production", "produccion")


def login(db: Session, usuario: str, clave: str, ip: str | None = None):
    with log_execution(db, "login", "web", {"usuario": usuario}):
        u = db.query(Usuario).filter(Usuario.usuario == usuario).first()
        if not u or not verify_password(clave, u.clave_hash):
            raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
        if u.activo is False:
            raise HTTPException(status_code=403, detail="El usuario está desactivado. Contacte al administrador.")
        rol = normalizar_rol(u.rol)
        token = create_token(u.id, u.usuario, rol)
        s = Sesion(usuario_id=u.id, token_jti=token[-32:],
                   expira=datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES))
        db.add(s); db.commit()
        return {"token": token, "nombre": u.usuario, "rol": rol}


def bootstrap_admin(db: Session):
    """Crea el usuario `admin` si no existe. En producción exige ADMIN_PASSWORD."""
    if db.query(Usuario).filter(Usuario.usuario == "admin").first():
        return
    clave = settings.ADMIN_PASSWORD
    if not clave:
        if es_produccion():
            log.error("No se creó el usuario admin: defina ADMIN_PASSWORD en las variables de entorno.")
            return
        clave = CLAVE_DEV
    db.add(Usuario(usuario="admin", clave_hash=hash_password(clave), rol="admin", activo=True))
    db.commit()


def validar_configuracion_produccion():
    """Falla al arrancar si en producción quedan secretos por defecto."""
    if not es_produccion():
        return
    problemas = []
    if settings.JWT_SECRET in ("", "change-me", "change-me-to-a-long-random-string") or len(settings.JWT_SECRET) < 32:
        problemas.append("JWT_SECRET debe ser un valor aleatorio de al menos 32 caracteres")
    if settings.ADMIN_PASSWORD and len(settings.ADMIN_PASSWORD) < 10:
        problemas.append("ADMIN_PASSWORD debe tener al menos 10 caracteres")
    if "*" in settings.ALLOWED_ORIGINS:
        problemas.append("ALLOWED_ORIGINS no puede ser '*' en producción")
    if problemas:
        raise RuntimeError("Configuración insegura para producción: " + "; ".join(problemas))

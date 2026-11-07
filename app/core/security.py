from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.db import get_db

bearer = HTTPBearer(auto_error=False)

def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False

def create_token(usuario_id: int, usuario: str, rol: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    return jwt.encode({"sub": str(usuario_id), "usuario": usuario, "rol": rol, "exp": exp},
                      settings.JWT_SECRET, algorithm="HS256")

def get_current_user(creds: HTTPAuthorizationCredentials = Depends(bearer),
                     db: Session = Depends(get_db)):
    if not creds:
        raise HTTPException(status_code=401, detail="Sesión no válida")
    try:
        payload = jwt.decode(creds.credentials, settings.JWT_SECRET, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Sesión no válida o vencida")
    # Se consulta el usuario en cada petición: así un cambio de rol o una desactivación
    # tiene efecto inmediato, sin esperar a que venza el token.
    from app.models import Usuario
    u = db.query(Usuario).filter(Usuario.id == int(payload["sub"])).first()
    if not u or u.activo is False:
        raise HTTPException(status_code=401, detail="Sesión no válida o usuario desactivado")
    return {"id": u.id, "usuario": u.usuario, "rol": normalizar_rol(u.rol)}


# --- Roles y permisos ------------------------------------------------------------
# admin:    todo, incluida la administración del motor, usuarios, auditoría y alertas.
# operador: carga documentos e imágenes, confirma sus facturas y usa el chatbot.
# consulta: solo lectura (documentos, búsqueda, chatbot y facturas), sin subir ni confirmar.
ROLES = ("admin", "operador", "consulta")
PERMISOS: dict[str, set[str]] = {
    "ver_documentos":      {"admin", "operador", "consulta"},
    "subir_documentos":    {"admin", "operador"},
    "eliminar_documentos": {"admin"},
    "usar_chatbot":        {"admin", "operador", "consulta"},
    "subir_imagenes":      {"admin", "operador"},
    "confirmar_facturas":  {"admin", "operador"},
    "ver_facturas":        {"admin", "operador", "consulta"},
    "ver_todas_facturas":  {"admin", "consulta"},
    "ver_alertas":         {"admin", "operador"},
    "atender_alertas":     {"admin"},
    "ejecutar_flujos":     {"admin"},
    "ver_ejecuciones":     {"admin"},
    "ver_auditoria":       {"admin"},
    "gestionar_usuarios":  {"admin"},
}


def normalizar_rol(rol: str | None) -> str:
    return "operador" if rol in (None, "", "usuario") else rol   # "usuario" es el rol heredado


def tiene_permiso(usuario: dict, permiso: str) -> bool:
    return usuario["rol"] in PERMISOS.get(permiso, set())


def require_permiso(permiso: str):
    """Dependencia: exige que el rol del usuario tenga el permiso indicado."""
    def _check(u=Depends(get_current_user)):
        if not tiene_permiso(u, permiso):
            raise HTTPException(status_code=403, detail="Su rol no tiene permiso para esta acción")
        return u
    return _check


def require_role(*roles: str):
    """Dependencia: exige que el usuario autenticado tenga uno de los roles dados."""
    def _check(u=Depends(get_current_user)):
        if u["rol"] not in roles:
            raise HTTPException(status_code=403, detail="No tiene permisos para esta acción")
        return u
    return _check

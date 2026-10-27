from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.models import Usuario, Sesion
from app.core.security import verify_password, create_token, hash_password
from app.core.logging import log_execution
from datetime import datetime, timezone, timedelta
from app.core.config import settings

def login(db: Session, usuario: str, clave: str, ip: str | None = None):
    with log_execution(db, "login", "web", {"usuario": usuario}):
        u = db.query(Usuario).filter(Usuario.usuario == usuario).first()
        if not u or not verify_password(clave, u.clave_hash):
            raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
        token = create_token(u.id, u.usuario, u.rol)
        jti = token[-32:]
        s = Sesion(usuario_id=u.id, token_jti=jti,
                   expira=datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES))
        db.add(s); db.commit()
        return {"token": token, "nombre": u.usuario, "rol": u.rol}

def bootstrap_admin(db: Session):
    if not db.query(Usuario).filter(Usuario.usuario == "admin").first():
        db.add(Usuario(usuario="admin", clave_hash=hash_password("Cambiar-2026"), rol="admin"))
        db.commit()

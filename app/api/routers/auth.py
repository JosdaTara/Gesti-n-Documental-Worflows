from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.core.security import PERMISOS, get_current_user
from app.schemas import LoginIn, LoginOut
from app.services.auth_service import login

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginOut)
def do_login(body: LoginIn, db: Session = Depends(get_db)):
    return login(db, body.usuario, body.clave)


@router.get("/me")
def yo(u=Depends(get_current_user)):
    """Usuario actual y sus permisos (el frontend los usa para mostrar u ocultar opciones)."""
    return {**u, "permisos": sorted(p for p, roles in PERMISOS.items() if u["rol"] in roles)}

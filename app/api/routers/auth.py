from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.core.security import get_current_user
from app.schemas import LoginIn, LoginOut
from app.services.auth_service import login, bootstrap_admin

router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.post("/login", response_model=LoginOut)
def do_login(body: LoginIn, db: Session = Depends(get_db)):
    return login(db, body.usuario, body.clave)

@router.post("/bootstrap")
def bootstrap(db: Session = Depends(get_db)):
    bootstrap_admin(db)
    return {"ok": True}

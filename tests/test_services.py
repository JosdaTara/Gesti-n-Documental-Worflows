import os
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("GEMINI_API_KEY", "")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.db import Base
from app.models import Usuario, Repositorio
from app.core.security import hash_password, verify_password, create_token
from app.services.auth_service import login
from fastapi.testclient import TestClient
from app.main import app

engine = create_engine("sqlite:///./test.db")
Base.metadata.create_all(bind=engine)
Session = sessionmaker(bind=engine)

def setup_module(_):
    db = Session()
    db.add(Usuario(usuario="admin", clave_hash=hash_password("Cambiar-2026"), rol="admin"))
    db.add(Repositorio(nombre="General"))
    db.commit(); db.close()

def test_password_hash_roundtrip():
    h = hash_password("x")
    assert verify_password("x", h) and not verify_password("y", h)

def test_login_ok():
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"usuario": "admin", "clave": "Cambiar-2026"})
    assert r.status_code == 200
    assert r.json()["rol"] == "admin" and r.json()["token"]

def test_login_invalid():
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"usuario": "admin", "clave": "wrong"})
    assert r.status_code == 401

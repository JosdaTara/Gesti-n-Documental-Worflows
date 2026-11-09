from fastapi.testclient import TestClient
from app.core.db import SessionLocal
from app.core.security import hash_password, verify_password
from app.models import Usuario, Repositorio
from app.main import app


def setup_module(_):
    db = SessionLocal()
    if not db.query(Usuario).filter(Usuario.usuario == "admin").first():
        db.add(Usuario(usuario="admin", clave_hash=hash_password("Cambiar-2026"), rol="admin"))
    if not db.query(Repositorio).filter(Repositorio.nombre == "General").first():
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


def test_dos_inicios_de_sesion_en_el_mismo_segundo():
    """Antes el 2.º devolvía 500: el token salía idéntico y chocaba con UNIQUE(token_jti)."""
    c = TestClient(app)
    r1 = c.post("/api/auth/login", json={"usuario": "admin", "clave": "Cambiar-2026"})
    r2 = c.post("/api/auth/login", json={"usuario": "admin", "clave": "Cambiar-2026"})
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["token"] != r2.json()["token"]

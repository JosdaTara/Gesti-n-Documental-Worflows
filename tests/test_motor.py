"""Pruebas del motor de automatización (Fase 1): registro, reintentos y flujos WF1/WF2."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.db import SessionLocal
from app.core.logging import con_reintentos, log_execution
from app.core.security import create_token, hash_password
from app.main import app
from app.models import Documento, Ejecucion, Usuario
from app.services import document_service, indexing_service, storage

client = TestClient(app)


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


def _ultima(db, flujo):
    return db.query(Ejecucion).filter(Ejecucion.flujo == flujo).order_by(Ejecucion.id.desc()).first()


def _usuario(db, nombre, rol):
    u = db.query(Usuario).filter(Usuario.usuario == nombre).first()
    if not u:
        u = Usuario(usuario=nombre, clave_hash=hash_password("x"), rol=rol)
        db.add(u); db.commit(); db.refresh(u)
    return {"Authorization": f"Bearer {create_token(u.id, u.usuario, u.rol)}"}


def _doc_pendiente(db, nombre="a.txt", contenido=b"hola mundo"):
    key, h = storage.save_bytes(contenido, "text/plain", "test")
    d = Documento(storage_key=key, nombre=nombre, tipo="TXT", estado="pendiente", hash_sha256=h)
    db.add(d); db.commit(); db.refresh(d)
    return d


# --- registro de ejecuciones -------------------------------------------------

def test_log_execution_ok_guarda_entrada_resultado_y_duracion(db):
    with log_execution(db, "prueba_ok", "test", {"x": 1}) as rec:
        rec.resultado = {"filas": 3}
    e = _ultima(db, "prueba_ok")
    assert e.entrada == {"x": 1}
    assert e.resultado == {"ok": True, "filas": 3}
    assert e.error is None and e.fin is not None and e.duracion_ms >= 0


def test_log_execution_error_se_registra_y_se_propaga(db):
    with pytest.raises(RuntimeError):
        with log_execution(db, "prueba_error", "test"):
            raise RuntimeError("boom")
    e = _ultima(db, "prueba_error")
    assert e.resultado == {"ok": False} and "boom" in e.error


def test_log_execution_registra_rechazos_http(db):
    with pytest.raises(HTTPException):
        with log_execution(db, "prueba_http", "test"):
            raise HTTPException(status_code=403, detail="sin permiso")
    e = _ultima(db, "prueba_http")
    assert e.resultado == {"ok": False, "status": 403} and e.error == "sin permiso"


# --- reintentos --------------------------------------------------------------

def test_reintenta_fallos_transitorios_y_los_anota(db):
    llamadas = {"n": 0}

    @con_reintentos
    def inestable():
        llamadas["n"] += 1
        if llamadas["n"] < 3:
            raise ConnectionError("red caída")
        return "ok"

    with log_execution(db, "prueba_reintento", "test"):
        assert inestable() == "ok"
    assert llamadas["n"] == 3
    assert _ultima(db, "prueba_reintento").resultado["reintentos"] == 2


def test_no_reintenta_errores_de_datos():
    llamadas = {"n": 0}

    @con_reintentos
    def malo():
        llamadas["n"] += 1
        raise ValueError("dato inválido")

    with pytest.raises(ValueError):
        malo()
    assert llamadas["n"] == 1


def test_agota_reintentos_y_propaga():
    llamadas = {"n": 0}

    @con_reintentos
    def siempre_falla():
        llamadas["n"] += 1
        raise ConnectionError("sin servicio")

    with pytest.raises(ConnectionError):
        siempre_falla()
    assert llamadas["n"] == 3


# --- WF1: indexación ---------------------------------------------------------

def test_indexar_documento_ok(db, monkeypatch):
    monkeypatch.setattr(indexing_service, "_embed", lambda chunks: [[0.0] * 3 for _ in chunks])
    monkeypatch.setattr(indexing_service, "_gemini_json",
                        lambda p: '{"categoria":"Informe","resumen":"r","datos":{}}')
    d = _doc_pendiente(db)
    res = indexing_service.index_one(db, str(d.id), "test")
    db.refresh(d)
    assert d.estado == "procesado" and d.categoria == "Informe"
    assert res == {"chunks": 1, "categoria": "Informe"}
    assert _ultima(db, "indexar_documento").resultado["ok"] is True


def test_indexar_documento_falla_marca_error(db, monkeypatch):
    def caida(_):
        raise ConnectionError("Gemini no responde")
    monkeypatch.setattr(indexing_service, "_embed", caida)
    d = _doc_pendiente(db)
    with pytest.raises(ConnectionError):
        indexing_service.index_one(db, str(d.id), "test")
    db.refresh(d)
    assert d.estado == "error" and "Gemini" in d.detalle
    assert "Gemini" in _ultima(db, "indexar_documento").error


def test_barrido_cuenta_procesados_y_errores(db, monkeypatch):
    db.query(Documento).filter(Documento.estado == "pendiente").update({"estado": "procesado"})
    db.commit()
    monkeypatch.setattr(indexing_service, "_gemini_json", lambda p: '{"categoria":"Otro"}')

    def embed(chunks):
        if "falla" in chunks[0]:
            raise ConnectionError("x")
        return [[0.0] for _ in chunks]
    monkeypatch.setattr(indexing_service, "_embed", embed)
    _doc_pendiente(db, contenido=b"bien")
    _doc_pendiente(db, contenido=b"falla")
    res = indexing_service.sweep_pending(db, "test")
    assert res == {"pendientes": 2, "procesados": 1, "errores": 1}
    assert _ultima(db, "barrido_indexacion").resultado["procesados"] == 1


# --- WF2: gestión y endpoints de administración -------------------------------

def test_eliminar_requiere_admin_y_queda_registrado(db):
    d = _doc_pendiente(db)
    with pytest.raises(HTTPException):
        document_service.eliminar(db, str(d.id), "usuario", 99)
    e = _ultima(db, "eliminar_documento")
    assert e.resultado["status"] == 403


def test_run_indexer_permisos(db, monkeypatch):
    monkeypatch.setattr(indexing_service, "sweep_pending", lambda db, disp: {"disparador": disp})
    assert client.post("/api/admin/run-indexer").status_code == 401
    assert client.post("/api/admin/run-indexer", headers=_usuario(db, "ana", "usuario")).status_code == 403
    r = client.post("/api/admin/run-indexer", headers=_usuario(db, "admin2", "admin"))
    assert r.status_code == 200 and r.json()["disparador"] == "manual"
    r = client.post("/api/admin/run-indexer", headers={"X-Cron-Token": "cron-test"})
    assert r.status_code == 200 and r.json()["disparador"] == "cron"
    assert client.post("/api/admin/run-indexer", headers={"X-Cron-Token": "malo"}).status_code == 401


def test_listar_ejecuciones_filtra(db):
    h = _usuario(db, "admin2", "admin")
    r = client.get("/api/admin/ejecuciones?estado=error&limit=5", headers=h)
    assert r.status_code == 200
    filas = r.json()["ejecuciones"]
    assert filas and all(f["error"] for f in filas)
    assert client.get("/api/admin/ejecuciones", headers=_usuario(db, "ana", "usuario")).status_code == 403

"""Pruebas de los flujos migrados desde n8n que faltaban: WF2 (gestión), WF3 (RAG) y el scheduler."""
import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal
from app.core.security import create_token, hash_password
from app.main import app
from app.models import Documento, Ejecucion, Usuario
from app.services import indexing_service, llm, rag_service
from app.workers import scheduler

client = TestClient(app)


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


def _h(db, nombre="wf_oper", rol="operador"):
    u = db.query(Usuario).filter(Usuario.usuario == nombre).first()
    if not u:
        u = Usuario(usuario=nombre, clave_hash=hash_password("x"), rol=rol)
        db.add(u); db.commit(); db.refresh(u)
    return {"Authorization": f"Bearer {create_token(u.id, u.usuario, u.rol)}"}


def _ultima(db, flujo):
    db.expire_all()
    return db.query(Ejecucion).filter(Ejecucion.flujo == flujo).order_by(Ejecucion.id.desc()).first()


# --- WF2: API de gestión ----------------------------------------------------------------

def test_subir_listar_descargar_y_crear_repo(db):
    h = _h(db)
    assert client.post("/api/repositories", headers=h, json={"nombre": "Contratos WF"}).status_code == 200
    assert client.post("/api/repositories", headers=h, json={"nombre": "Contratos WF"}).status_code == 400
    r = client.post("/api/documents", headers=h, data={"repo": "Contratos WF"},
                    files={"file": ("contrato.txt", b"Contrato de suministro por 12 meses", "text/plain")})
    assert r.status_code == 200
    doc_id = r.json()["id"]
    assert _ultima(db, "subir_documento").resultado["id"] == doc_id

    lista = client.get("/api/documents", headers=h).json()
    doc = next(d for d in lista["documentos"] if d["id"] == doc_id)
    assert doc["repo"] == "Contratos WF" and doc["estado"] == "pendiente" and "Contratos WF" in lista["repos"]

    url = client.get(f"/api/documents/{doc_id}/download", headers=h).json()["url"]
    assert url.endswith(".bin")
    assert client.get("/api/documents/no-es-un-uuid/download", headers=h).status_code == 404


def test_subida_valida_formato_y_tamano(db):
    h = _h(db)
    r = client.post("/api/documents", headers=h, data={"repo": "General"},
                    files={"file": ("virus.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400 and "formato" in r.json()["detail"]


def test_reintentar_reindexa_y_reporta_el_estado(db, monkeypatch):
    h = _h(db)
    r = client.post("/api/documents", headers=h, data={"repo": "General"},
                    files={"file": ("informe.txt", b"Informe trimestral de ventas", "text/plain")})
    doc_id = r.json()["id"]
    monkeypatch.setattr(indexing_service, "_embed", lambda chunks: [[0.1] * 768 for _ in chunks])
    monkeypatch.setattr(indexing_service, "_llm_json", lambda p: {"categoria": "Informe", "resumen": "Ventas T3"})
    r = client.post(f"/api/documents/{doc_id}/retry", headers=h)
    assert r.json() == {"ok": True, "estado": "procesado", "detalle": ""}
    assert _ultima(db, "indexar_documento").disparador == "reintento_manual"


def test_admin_elimina_documento(db):
    h = _h(db)
    doc_id = client.post("/api/documents", headers=h, data={"repo": "General"},
                         files={"file": ("borrar.txt", b"temporal", "text/plain")}).json()["id"]
    assert client.delete(f"/api/documents/{doc_id}", headers=h).status_code == 403
    assert client.delete(f"/api/documents/{doc_id}", headers=_h(db, "wf_admin", "admin")).status_code == 200
    db.expire_all()
    assert db.query(Documento).filter(Documento.id == __import__("uuid").UUID(doc_id)).first() is None


# --- WF3: RAG ------------------------------------------------------------------------------

def test_rag_responde_con_fuentes_y_registra(db, monkeypatch):
    hits = [{"archivo": "contrato.pdf", "fragmento": "La vigencia es de 12 meses.", "pagina": None},
            {"archivo": "contrato.pdf", "fragmento": "Renovación automática.", "pagina": None}]
    monkeypatch.setattr(rag_service, "_buscar", lambda db, q, repo="": hits)
    monkeypatch.setattr(rag_service, "_generar", lambda prompt, uid=None: "La vigencia es de 12 meses [1].")
    h = _h(db)
    r = client.post("/api/rag/consultar", headers=h, json={"pregunta": "¿Vigencia?"}).json()
    assert r["respuesta"].startswith("La vigencia") and r["fuentes"] == [{"archivo": "contrato.pdf"}]
    assert _ultima(db, "rag_consultar").resultado == {"ok": True, "fuentes": 1}
    r = client.post("/api/search", headers=h, json={"q": "vigencia"}).json()
    assert len(r["resultados"]) == 2 and _ultima(db, "rag_buscar").resultado["resultados"] == 2


def test_rag_sin_fragmentos_no_llama_al_modelo(db, monkeypatch):
    monkeypatch.setattr(rag_service, "_buscar", lambda db, q, repo="": [])
    monkeypatch.setattr(rag_service, "_generar", lambda *a: pytest.fail("no debía llamar al LLM"))
    r = rag_service.answer(db, "¿algo?")
    assert r["fuentes"] == [] and "No encontré" in r["respuesta"]


def test_rag_sin_ia_responde_503(db, monkeypatch):
    def sin_ia(*a, **k):
        raise llm.LLMNoDisponible("El asistente de IA no está disponible en este momento.")
    monkeypatch.setattr(rag_service, "_buscar", sin_ia)
    r = client.post("/api/search", headers=_h(db), json={"q": "x"})
    assert r.status_code == 503 and "no está disponible" in r.json()["detail"]
    assert "LLMNoDisponible" in _ultima(db, "rag_buscar").error


# --- scheduler ------------------------------------------------------------------------------

def test_tarea_programada_ejecuta_ambos_barridos_aunque_uno_falle(monkeypatch):
    llamados = []

    def indexacion(db, disp):
        llamados.append(("indexacion", disp)); raise RuntimeError("Gemini caído")

    def facturas(db, disp):
        llamados.append(("facturas", disp)); return {}
    monkeypatch.setattr(indexing_service, "sweep_pending", indexacion)
    from app.services import automatizacion_facturas
    monkeypatch.setattr(automatizacion_facturas, "barrido_pendientes", facturas)
    scheduler._job()
    assert llamados == [("indexacion", "cron"), ("facturas", "cron")]


def test_scheduler_arranca_una_sola_vez_y_se_apaga():
    scheduler.start(); primero = scheduler._sched
    scheduler.start()
    assert scheduler._sched is primero and primero.get_job("indexer") is not None
    scheduler.shutdown()
    assert scheduler._sched is None

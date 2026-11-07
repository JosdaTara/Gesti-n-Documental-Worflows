"""Pruebas de la capa de IA (Fase 2): plan B entre proveedores, auditoría y chatbot LangGraph.

No se llama a ningún servicio real: los modelos se sustituyen por modelos falsos de LangChain.
"""
import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from app.core.db import SessionLocal
from app.core.security import create_token, hash_password
from app.main import app
from app.models import Auditoria, Conversacion, Factura, Usuario
from app.services import chatbot, llm

client = TestClient(app)


class ModeloFalso(GenericFakeChatModel):
    """Modelo falso que acepta herramientas (create_agent llama a bind_tools)."""
    def bind_tools(self, tools, **kwargs):
        return self


class ModeloCaido(ModeloFalso):
    def _generate(self, *a, **k):
        raise ConnectionError("429 cuota agotada")


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def usuario(db):
    u = db.query(Usuario).filter(Usuario.usuario == "chat_user").first()
    if not u:
        u = Usuario(usuario="chat_user", clave_hash=hash_password("x"), rol="usuario")
        db.add(u); db.commit(); db.refresh(u)
    return u


def _headers(u):
    return {"Authorization": f"Bearer {create_token(u.id, u.usuario, u.rol)}"}


def _proveedores(monkeypatch, modelos: dict):
    """Simula los proveedores configurados: {nombre: fábrica_de_modelo}."""
    monkeypatch.setattr(llm, "proveedores_configurados", lambda: list(modelos))
    monkeypatch.setattr(llm, "crear_modelo", lambda p, t=0.2: modelos[p]())


def _ultima_auditoria(db, accion):
    return db.query(Auditoria).filter(Auditoria.accion == accion).order_by(Auditoria.id.desc()).first()


# --- plan B ----------------------------------------------------------------------

def test_plan_b_usa_groq_si_gemini_falla(db, monkeypatch):
    _proveedores(monkeypatch, {
        "gemini": lambda: ModeloCaido(messages=iter([])),
        "groq": lambda: ModeloFalso(messages=iter([AIMessage("respuesta de groq")])),
    })
    r = llm.invocar("hola", accion="prueba_plan_b", usuario_id=1)
    assert r.proveedor == "groq" and r.texto == "respuesta de groq"
    assert r.fallos and r.fallos[0].startswith("gemini")
    a = _ultima_auditoria(db, "ia:prueba_plan_b")
    assert a.detalle["proveedor"] == "groq" and a.detalle["fallos_previos"]


def test_todos_los_proveedores_fallan(monkeypatch):
    _proveedores(monkeypatch, {"gemini": lambda: ModeloCaido(messages=iter([])),
                               "groq": lambda: ModeloCaido(messages=iter([]))})
    with pytest.raises(llm.LLMNoDisponible) as e:
        llm.invocar("hola", accion="x")
    assert len(e.value.fallos) == 2


def test_sin_proveedores_configurados(monkeypatch):
    monkeypatch.setattr(llm, "proveedores_configurados", lambda: [])
    with pytest.raises(llm.LLMNoDisponible):
        llm.invocar("hola", accion="x")


def test_embed_sin_clave_no_reintenta(monkeypatch):
    monkeypatch.setattr(llm.settings, "GEMINI_API_KEY", "")
    with pytest.raises(llm.LLMNoDisponible):
        llm.embed(["texto"])


def test_proveedores_respetan_orden_y_configuracion(monkeypatch):
    monkeypatch.setattr(llm.settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(llm.settings, "GROQ_API_KEY", "k")
    monkeypatch.setattr(llm.settings, "OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setattr(llm.settings, "LLM_PROVIDERS", "ollama,groq,gemini")
    assert llm.proveedores_configurados() == ["ollama", "groq"]


def test_extraer_json_tolera_bloques_de_codigo():
    assert llm.extraer_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.extraer_json('Claro: {"b": 2} listo') == {"b": 2}
    with pytest.raises(ValueError):
        llm.extraer_json("sin json")


# --- chatbot ---------------------------------------------------------------------

def test_chatbot_usa_herramienta_guarda_memoria_y_audita(db, usuario, monkeypatch):
    db.add(Factura(proveedor="ACME SAS", nit="900123", total=150000, estado="validada"))
    db.commit()
    llamada = AIMessage("", tool_calls=[{"name": "consultar_facturas", "args": {"proveedor": "ACME"},
                                         "id": "t1", "type": "tool_call"}])
    _proveedores(monkeypatch, {"gemini": lambda: ModeloFalso(
        messages=iter([llamada, AIMessage("ACME SAS tiene una factura por 150000.")]))})

    r = chatbot.responder(db, usuario.id, "¿Qué facturas tiene ACME?", "s1")
    assert r["respuesta"] == "ACME SAS tiene una factura por 150000."
    assert r["proveedor"] == "gemini"

    filas = db.query(Conversacion).filter(Conversacion.usuario_id == usuario.id,
                                          Conversacion.sesion == "s1").all()
    assert [f.rol for f in filas] == ["user", "assistant"]
    a = _ultima_auditoria(db, "ia:chatbot")
    assert a.detalle["herramientas"] == ["consultar_facturas"]


def test_chatbot_recuerda_la_conversacion(db, usuario, monkeypatch):
    vistos = []

    class Espia(ModeloFalso):
        def _generate(self, messages, *a, **k):
            vistos.append([m.content for m in messages])
            return super()._generate(messages, *a, **k)

    _proveedores(monkeypatch, {"gemini": lambda: Espia(messages=iter([AIMessage("Te llamas Ana.")]))})
    chatbot.borrar_historial(db, usuario.id, "memoria")
    db.add_all([Conversacion(usuario_id=usuario.id, sesion="memoria", rol="user", contenido="Me llamo Ana"),
                Conversacion(usuario_id=usuario.id, sesion="memoria", rol="assistant", contenido="Hola Ana")])
    db.commit()
    chatbot.responder(db, usuario.id, "¿Cómo me llamo?", "memoria")
    assert "Me llamo Ana" in vistos[0] and "Hola Ana" in vistos[0]


def test_chat_responde_503_claro_si_no_hay_ia(usuario, monkeypatch):
    monkeypatch.setattr(llm, "proveedores_configurados", lambda: [])
    r = client.post("/api/chat", json={"pregunta": "hola", "sesion": "x"}, headers=_headers(usuario))
    assert r.status_code == 503 and "no hay ningún modelo" in r.json()["detail"].lower()


def test_historial_del_chat_por_usuario(db, usuario, monkeypatch):
    _proveedores(monkeypatch, {"groq": lambda: ModeloFalso(messages=iter([AIMessage("ok")]))})
    h = _headers(usuario)
    client.delete("/api/chat/historial?sesion=h1", headers=h)
    assert client.post("/api/chat", json={"pregunta": "hola", "sesion": "h1"}, headers=h).json()["proveedor"] == "groq"
    msgs = client.get("/api/chat/historial?sesion=h1", headers=h).json()["mensajes"]
    assert [m["rol"] for m in msgs] == ["user", "assistant"] and msgs[1]["proveedor"] == "groq"
    assert client.delete("/api/chat/historial?sesion=h1", headers=h).json()["borrados"] == 2

"""Pruebas de la Fase 4: visión → automatización, alertas, roles, chatbot con fotos y seguridad."""
import io
import itertools
import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from PIL import Image

from app.core.db import SessionLocal
from app.core.security import create_token, hash_password
from app.main import app
from app.models import Alerta, Conversacion, Ejecucion, Factura, Proveedor, Usuario
from app.services import automatizacion_facturas, chatbot, llm, vision_service
from app.services.validacion_factura import digito_verificacion

client = TestClient(app)
_colores = itertools.count(1)
_bases = itertools.count(811000001)


class Modelo(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def nit_valido() -> str:
    base = str(next(_bases))
    return f"{base}-{digito_verificacion(base)}"


def factura_json(nit, total=35700, fecha="2026-10-20", proveedor="Proveedor Prueba SAS", conf=0.95):
    return {"proveedor": proveedor, "nit": nit, "fecha": fecha, "moneda": "COP", "items": [], "total": total,
            "confianza_campos": {"proveedor": conf, "nit": conf, "fecha": conf, "total": conf, "items": 0.9}}


def _jpeg() -> bytes:
    n = next(_colores)
    out = io.BytesIO()
    Image.new("RGB", (120, 90), (n * 37 % 256, n * 91 % 256, n * 53 % 256)).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def vision_falsa(monkeypatch):
    """Permite fijar lo que 'lee' la IA en cada subida."""
    respuestas = []
    monkeypatch.setattr(vision_service, "anonimizar_imagen", lambda img: (img, []))
    monkeypatch.setattr(llm, "proveedores_configurados", lambda orden=None: ["gemini"])
    monkeypatch.setattr(llm, "crear_modelo", lambda p, t=0.2, vision=False:
                        Modelo(messages=iter([AIMessage(json.dumps(respuestas.pop(0)))])))
    return respuestas


def usuario(db, nombre, rol):
    u = db.query(Usuario).filter(Usuario.usuario == nombre).first()
    if not u:
        u = Usuario(usuario=nombre, clave_hash=hash_password("clave-segura-1"), rol=rol, activo=True)
        db.add(u); db.commit(); db.refresh(u)
    return u, {"Authorization": f"Bearer {create_token(u.id, u.usuario, u.rol)}"}


def subir(h, ruta="/api/vision/invoices", **extra):
    return client.post(ruta, headers=h, data={"consentimiento": "true", **extra},
                       files={"file": ("f.jpg", _jpeg(), "image/jpeg")})


def alertas_de(db, factura_id):
    db.expire_all()
    return sorted(a.tipo for a in db.query(Alerta).filter(Alerta.entidad_id == factura_id).all())


# --- visión dispara la automatización ------------------------------------------------

def test_factura_validada_se_procesa_sola_y_registra_proveedor(db, vision_falsa):
    nit = nit_valido()
    vision_falsa.append(factura_json(nit, proveedor="Papelería Central SAS"))
    _, h = usuario(db, "int_oper", "operador")
    f = subir(h).json()
    assert f["estado"] == "validada" and f["procesada"] is True
    assert [a["tipo"] for a in f["alertas"]] == ["proveedor_nuevo"]
    p = db.query(Proveedor).filter(Proveedor.nit == nit).first()
    assert p.nombre == "Papelería Central SAS" and p.facturas == 1 and p.total_compras == 35700
    ej = db.query(Ejecucion).filter(Ejecucion.flujo == "procesar_factura").order_by(Ejecucion.id.desc()).first()
    assert ej.disparador == "vision" and ej.resultado["proveedor_nuevo"] is True


def test_reglas_duplicado_y_monto_alto(db, vision_falsa):
    nit = nit_valido()
    _, h = usuario(db, "int_oper", "operador")
    vision_falsa.append(factura_json(nit, total=100000))
    subir(h)
    vision_falsa.append(factura_json(nit, total=100000))          # mismo NIT, fecha y total
    dup = subir(h).json()
    assert alertas_de(db, dup["id"]) == ["posible_duplicado"]
    vision_falsa.append(factura_json(nit, total=9_000_000, fecha="2026-10-21"))
    alta = subir(h).json()
    assert "monto_alto" in alertas_de(db, alta["id"])


def test_monto_atipico_respecto_al_promedio_del_proveedor(db, vision_falsa):
    nit = nit_valido()
    _, h = usuario(db, "int_oper", "operador")
    for dia in (1, 2, 3):
        vision_falsa.append(factura_json(nit, total=10000, fecha=f"2026-09-0{dia}"))
        subir(h)
    vision_falsa.append(factura_json(nit, total=50000, fecha="2026-09-10"))
    f = subir(h).json()
    assert alertas_de(db, f["id"]) == ["monto_atipico"]


def test_confirmacion_humana_dispara_el_flujo(db, vision_falsa):
    _, h = usuario(db, "int_oper", "operador")
    vision_falsa.append(factura_json("800197268-5", conf=0.6))     # DV inválido → por_confirmar
    f = subir(h).json()
    assert f["estado"] == "por_confirmar" and f["procesada"] is False and f["alertas"] == []
    r = client.post(f"/api/vision/invoices/{f['id']}/confirm", headers=h,
                    json={"proveedor": "Proveedor Prueba SAS", "nit": "800197268-5", "fecha": "2026-10-20", "total": 35700})
    assert r.json()["procesada"] is True
    assert "nit_invalido" in alertas_de(db, f["id"])
    ej = db.query(Ejecucion).filter(Ejecucion.flujo == "procesar_factura").order_by(Ejecucion.id.desc()).first()
    assert ej.disparador == "confirmacion"


def test_flujo_idempotente_y_cola_de_reintento(db, vision_falsa, monkeypatch):
    _, h = usuario(db, "int_oper", "operador")
    original = automatizacion_facturas.procesar_factura

    def falla(*a, **k):
        raise RuntimeError("base de datos de compras caída")
    monkeypatch.setattr(automatizacion_facturas, "procesar_factura", falla)
    vision_falsa.append(factura_json(nit_valido()))
    f = subir(h).json()
    assert f["estado"] == "validada" and f["procesada"] is False       # la factura no se pierde

    monkeypatch.setattr(automatizacion_facturas, "procesar_factura", original)
    res = automatizacion_facturas.barrido_pendientes(db, "test")
    assert res["procesadas"] >= 1
    db.expire_all()
    assert db.query(Factura).filter(Factura.id == __import__("uuid").UUID(f["id"])).first().procesada_en
    assert original(db, f["id"], "test")["omitida"] == "ya procesada"


# --- roles y permisos --------------------------------------------------------------------

def test_rol_consulta_solo_lee(db, vision_falsa):
    _, h = usuario(db, "int_consulta", "consulta")
    assert client.get("/api/documents", headers=h).status_code == 200
    assert client.get("/api/vision/invoices", headers=h).status_code == 200
    assert client.post("/api/documents", headers=h, data={"repo": "General"},
                       files={"file": ("a.txt", b"hola", "text/plain")}).status_code == 403
    assert subir(h).status_code == 403
    assert subir(h, "/api/chat/imagen").status_code == 403
    assert client.get("/api/alertas", headers=h).status_code == 403
    me = client.get("/api/auth/me", headers=h).json()
    assert "subir_imagenes" not in me["permisos"] and "ver_facturas" in me["permisos"]


def test_consulta_ve_todas_las_facturas_operador_solo_las_suyas(db, vision_falsa):
    _, op1 = usuario(db, "int_op1", "operador")
    _, op2 = usuario(db, "int_op2", "operador")
    _, cons = usuario(db, "int_consulta", "consulta")
    vision_falsa.append(factura_json(nit_valido()))
    f = subir(op1).json()
    assert client.get(f"/api/vision/invoices/{f['id']}", headers=cons).status_code == 200
    assert client.get(f"/api/vision/invoices/{f['id']}", headers=op2).status_code == 404
    assert client.get(f"/api/vision/images/{f['imagen_id']}", headers=cons).status_code == 404  # imagen: solo dueño o admin


def test_solo_admin_gestiona_usuarios_y_atiende_alertas(db, vision_falsa):
    _, op = usuario(db, "int_oper", "operador")
    _, adm = usuario(db, "int_admin", "admin")
    assert client.get("/api/admin/usuarios", headers=op).status_code == 403
    vision_falsa.append(factura_json(nit_valido()))
    f = subir(op).json()
    alerta_id = f["alertas"][0]["id"]
    assert client.post(f"/api/alertas/{alerta_id}/atender", headers=op).status_code == 403
    r = client.post(f"/api/alertas/{alerta_id}/atender", headers=adm)
    assert r.status_code == 200 and r.json()["estado"] == "atendida"


def test_cambio_de_rol_y_desactivacion_tienen_efecto_inmediato(db):
    u, h = usuario(db, "int_cambiante", "operador")
    _, adm = usuario(db, "int_admin", "admin")
    assert client.get("/api/alertas", headers=h).status_code == 200
    assert client.patch(f"/api/admin/usuarios/{u.id}", headers=adm, json={"rol": "consulta"}).status_code == 200
    assert client.get("/api/alertas", headers=h).status_code == 403            # mismo token, rol nuevo
    client.patch(f"/api/admin/usuarios/{u.id}", headers=adm, json={"activo": False})
    assert client.get("/api/documents", headers=h).status_code == 401
    r = client.post("/api/auth/login", json={"usuario": "int_cambiante", "clave": "clave-segura-1"})
    assert r.status_code == 403


def test_admin_crea_usuarios_y_no_puede_degradarse(db):
    a, adm = usuario(db, "int_admin", "admin")
    r = client.post("/api/admin/usuarios", headers=adm, json={"usuario": "int_nuevo", "clave": "otraclave123", "rol": "consulta"})
    assert r.status_code == 200 and r.json()["rol"] == "consulta"
    assert client.post("/api/admin/usuarios", headers=adm, json={"usuario": "int_nuevo", "clave": "otraclave123"}).status_code == 400
    assert client.post("/api/admin/usuarios", headers=adm, json={"usuario": "x2", "clave": "corta"}).status_code == 422
    assert client.patch(f"/api/admin/usuarios/{a.id}", headers=adm, json={"rol": "operador"}).status_code == 400
    assert client.post("/api/auth/login", json={"usuario": "int_nuevo", "clave": "otraclave123"}).json()["rol"] == "consulta"


# --- chatbot -------------------------------------------------------------------------------

def test_foto_por_el_chat_registra_la_factura_y_responde(db, vision_falsa):
    u, h = usuario(db, "int_oper", "operador")
    vision_falsa.append(factura_json(nit_valido(), proveedor="Droguería La 80 SAS", total=48000))
    r = subir(h, "/api/chat/imagen", sesion="chat-foto")
    assert r.status_code == 200
    body = r.json()
    assert "Droguería La 80 SAS" in body["respuesta"] and "48.000" in body["respuesta"]
    assert "procesada automáticamente" in body["respuesta"] and "Nuevo proveedor" in body["respuesta"]
    assert body["factura"]["procesada"] is True
    msgs = db.query(Conversacion).filter(Conversacion.usuario_id == u.id, Conversacion.sesion == "chat-foto").all()
    assert [m.rol for m in msgs] == ["user", "assistant"]
    ej = db.query(Ejecucion).filter(Ejecucion.flujo == "vision_factura").order_by(Ejecucion.id.desc()).first()
    assert ej.disparador == "chatbot"


def test_herramientas_del_chatbot_segun_rol(db):
    _, _ = usuario(db, "int_consulta", "consulta")
    nombres = lambda u: [t.name for t in chatbot._herramientas(db, "", [], u)]
    assert "consultar_alertas" not in nombres({"id": 1, "rol": "consulta"})
    assert "consultar_alertas" in nombres({"id": 1, "rol": "admin"})
    resumen = next(t for t in chatbot._herramientas(db, "", [], None) if t.name == "resumen_compras")
    assert "Total general" in resumen.invoke({})


# --- seguridad --------------------------------------------------------------------------

def test_endpoint_bootstrap_publico_eliminado():
    assert client.post("/api/auth/bootstrap").status_code in (404, 405)


def test_produccion_exige_secretos(monkeypatch):
    from app.services import auth_service
    monkeypatch.setattr(auth_service.settings, "APP_ENV", "production")
    monkeypatch.setattr(auth_service.settings, "JWT_SECRET", "change-me")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        auth_service.validar_configuracion_produccion()
    monkeypatch.setattr(auth_service.settings, "JWT_SECRET", "x" * 40)
    auth_service.validar_configuracion_produccion()


def test_produccion_sin_admin_password_no_crea_admin_con_clave_por_defecto(monkeypatch):
    from app.services import auth_service

    class Q:
        def filter(self, *a): return self
        def first(self): return None

    class DB:
        agregado = []
        def query(self, *a): return Q()
        def add(self, x): DB.agregado.append(x)
        def commit(self): pass
    monkeypatch.setattr(auth_service.settings, "APP_ENV", "production")
    monkeypatch.setattr(auth_service.settings, "ADMIN_PASSWORD", "")
    auth_service.bootstrap_admin(DB())
    assert DB.agregado == []

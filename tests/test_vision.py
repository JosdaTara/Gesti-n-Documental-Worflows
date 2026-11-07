"""Pruebas del módulo de visión (Fase 3): validación, privacidad, plan B y API.

No se usa ningún servicio real: los modelos y Tesseract se sustituyen por dobles de prueba.
Las pruebas con fotos reales (20 imágenes, % de aciertos) van en la Fase 5.
"""
import io
import json
from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from PIL import Image

from app.core.db import SessionLocal
from app.core.security import create_token, hash_password
from app.main import app
from app.models import Auditoria, Ejecucion, Factura, Usuario
from app.services import imagen_utils, llm, privacidad, vision_service
from app.services.validacion_factura import digito_verificacion, evaluar, normalizar_fecha, \
    normalizar_nit, normalizar_numero, requiere_revision

client = TestClient(app)

BUENA = {"proveedor": "Ferretería El Tornillo SAS", "nit": "800197268-4", "fecha": "2026-10-20",
         "moneda": "COP", "items": [{"descripcion": "Tornillos", "cantidad": 2, "valor": 15000}],
         "total": 35700, "confianza_campos": {"proveedor": 0.95, "nit": 0.95, "fecha": 0.95, "total": 0.95, "items": 0.9}}


class Modelo(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class Espia(Modelo):
    """Guarda los mensajes que recibe para verificar qué se envió a la IA."""
    recibidos: list = []

    def _generate(self, messages, *a, **k):
        Espia.recibidos.append(messages)
        return super()._generate(messages, *a, **k)


class Caido(Modelo):
    def _generate(self, *a, **k):
        raise ConnectionError("429 cuota agotada")


def _jpeg(w=400, h=300, color="white", exif_orientacion=None) -> bytes:
    img = Image.new("RGB", (w, h), color)
    out = io.BytesIO()
    if exif_orientacion:
        exif = img.getexif(); exif[0x0112] = exif_orientacion
        img.save(out, "JPEG", exif=exif)
    else:
        img.save(out, "JPEG")
    return out.getvalue()


def _proveedores(monkeypatch, modelos: dict):
    monkeypatch.setattr(llm, "proveedores_configurados",
                        lambda orden=None: [p for p in modelos])
    monkeypatch.setattr(llm, "crear_modelo", lambda p, t=0.2, vision=False: modelos[p]())


def _sin_tesseract(monkeypatch):
    monkeypatch.setattr(privacidad, "ocr_disponible", lambda: False)


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


def _user(db, nombre, rol="usuario"):
    u = db.query(Usuario).filter(Usuario.usuario == nombre).first()
    if not u:
        u = Usuario(usuario=nombre, clave_hash=hash_password("x"), rol=rol)
        db.add(u); db.commit(); db.refresh(u)
    return u, {"Authorization": f"Bearer {create_token(u.id, u.usuario, u.rol)}"}


# --- validación y confianza por campo ---------------------------------------------

@pytest.mark.parametrize("nit,dv", [("800197268", 4), ("890903938", 8), ("860034313", 7)])
def test_digito_verificacion_dian(nit, dv):
    assert digito_verificacion(nit) == dv


def test_normalizar_nit():
    assert normalizar_nit("NIT 800.197.268-4") == ("800197268-4", "valido")
    assert normalizar_nit("8001972684") == ("800197268-4", "valido")
    assert normalizar_nit("800197268-5")[1] == "dv_invalido"
    assert normalizar_nit("80019726")[1] == "sin_dv"
    assert normalizar_nit(None)[1] == "vacio"


@pytest.mark.parametrize("entrada,esperado", [
    ("$1.234.567,50", 1234567.5), ("1,234,567.50", 1234567.5), ("35.700", 35700.0),
    ("35700", 35700.0), (35700, 35700.0), ("", None), ("abc", None)])
def test_normalizar_numero(entrada, esperado):
    assert normalizar_numero(entrada) == esperado


def test_normalizar_fecha():
    assert normalizar_fecha("2026-10-20") == date(2026, 10, 20)
    assert normalizar_fecha("20/10/2026") == date(2026, 10, 20)
    assert normalizar_fecha("31/02/2026") is None


def test_factura_completa_queda_validada():
    ev = evaluar(BUENA)  # 2 × 15000 = 30000 + IVA 19 % = 35700
    assert ev["confianza"] >= 0.9 and not ev["advertencias"]
    assert not requiere_revision(ev, 0.85)


def test_nit_con_dv_invalido_baja_la_confianza_y_pide_revision():
    ev = evaluar({**BUENA, "nit": "800197268-5"})
    assert ev["confianza_campos"]["nit"] < 0.5
    assert requiere_revision(ev, 0.85)
    assert any("dígito de verificación" in a for a in ev["advertencias"])


def test_total_incoherente_con_items():
    ev = evaluar({**BUENA, "total": 99999})
    assert ev["confianza_campos"]["total"] <= 0.6
    assert any("no coincide" in a for a in ev["advertencias"])


def test_campos_faltantes():
    ev = evaluar({"proveedor": "X SAS"})
    assert ev["confianza_campos"]["nit"] == 0 and requiere_revision(ev, 0.85)


# --- privacidad (Ley 1581) ---------------------------------------------------------

def test_enmascarar_texto_oculta_datos_personales_pero_no_el_nit():
    texto = ("Ferretería SAS NIT 800.197.268-4\nCliente: Ana Pérez C.C. 1.098.765.432\n"
             "Cel 310 555 1234 correo ana.perez@gmail.com")
    out, tipos = privacidad.enmascarar_texto(texto)
    assert "1.098.765.432" not in out and "310 555 1234" not in out and "gmail" not in out
    assert "800.197.268-4" in out
    assert set(tipos) == {"cedula", "telefono", "correo"}


def test_anonimizar_imagen_tapa_las_palabras_sensibles(monkeypatch):
    import pytesseract
    monkeypatch.setattr(privacidad, "ocr_disponible", lambda: True)
    datos = {"text": ["Correo:", "ana@x.com", "Total"], "block_num": [1, 1, 2], "par_num": [1, 1, 1],
             "line_num": [1, 1, 1], "left": [10, 100, 10], "top": [10, 10, 60],
             "width": [80, 120, 50], "height": [20, 20, 20]}
    monkeypatch.setattr(pytesseract, "image_to_data", lambda *a, **k: datos)
    salida, tipos = privacidad.anonimizar_imagen(_jpeg())
    img = Image.open(io.BytesIO(salida))
    assert tipos == ["correo"]
    assert img.getpixel((160, 20))[0] < 40      # el correo quedó tapado
    assert img.getpixel((30, 20))[0] > 200      # "Correo:" sigue visible


# --- preparación de imágenes ----------------------------------------------------------

def test_foto_de_celular_rotada_se_endereza_y_se_reduce():
    e = imagen_utils.preparar(_jpeg(3000, 1000, exif_orientacion=6), "image/jpeg")
    img = Image.open(io.BytesIO(e.imagen))
    assert img.size[1] > img.size[0]            # quedó vertical (EXIF aplicado)
    assert max(img.size) <= imagen_utils.LADO_MAX


def test_archivo_que_no_es_imagen_se_rechaza():
    with pytest.raises(HTTPException) as e:
        imagen_utils.preparar(b"no soy una imagen", "image/jpeg")
    assert e.value.status_code == 400
    with pytest.raises(HTTPException):
        imagen_utils.preparar(b"x", "text/plain")


# --- extracción con plan B ---------------------------------------------------------------

def test_sin_tesseract_no_se_envia_la_imagen_a_servicios_externos(monkeypatch):
    _sin_tesseract(monkeypatch)
    monkeypatch.setattr(vision_service.settings, "VISION_PRIVACY_MODE", "anonimizado")
    Espia.recibidos = []
    _proveedores(monkeypatch, {"gemini": lambda: Espia(messages=iter([AIMessage(json.dumps(BUENA))]))})
    r = vision_service.extraer(imagen_utils.Entrada(imagen=_jpeg()), consentimiento=True)
    assert Espia.recibidos == []                # gemini nunca recibió la imagen
    assert r["metodo"] == "manual"
    assert any("Tesseract" in a for a in r["advertencias"])


def test_vision_con_imagen_anonimizada(monkeypatch):
    monkeypatch.setattr(vision_service.settings, "VISION_PRIVACY_MODE", "anonimizado")
    monkeypatch.setattr(vision_service, "anonimizar_imagen", lambda img: (b"IMAGEN-ANONIMA", ["correo"]))
    Espia.recibidos = []
    _proveedores(monkeypatch, {"gemini": lambda: Espia(messages=iter([AIMessage(json.dumps(BUENA))]))})
    r = vision_service.extraer(imagen_utils.Entrada(imagen=_jpeg()), consentimiento=False)
    assert r["metodo"] == "vision:gemini" and r["datos_enmascarados"] == ["correo"]
    url = Espia.recibidos[0][0].content[1]["image_url"]["url"]
    import base64
    assert base64.b64decode(url.split(",", 1)[1]) == b"IMAGEN-ANONIMA"


def test_plan_b_ocr_con_texto_enmascarado(monkeypatch):
    monkeypatch.setattr(vision_service.settings, "VISION_PRIVACY_MODE", "externo")
    monkeypatch.setattr(vision_service, "ocr_texto", lambda img: "Tornillo SAS NIT 800197268-4 cel 3105551234 TOTAL 35700")
    # Visión: solo gemini (caído). Texto: groq (responde). Así se fuerza el plan B por OCR.
    monkeypatch.setattr(llm, "proveedores_configurados", lambda orden=None: ["gemini"] if orden else ["groq"])
    monkeypatch.setattr(llm, "crear_modelo", lambda p, t=0.2, vision=False:
                        Caido(messages=iter([])) if p == "gemini" else Espia(messages=iter([AIMessage(json.dumps(BUENA))])))
    Espia.recibidos = []
    r = vision_service.extraer(imagen_utils.Entrada(imagen=_jpeg()), consentimiento=True)
    assert r["metodo"] == "ocr+groq" and "telefono" in r["datos_enmascarados"]
    enviado = Espia.recibidos[0][-1].content
    assert "3105551234" not in enviado and "[TELEFONO]" in enviado and "800197268-4" in enviado


def test_modo_local_solo_usa_ollama(monkeypatch):
    monkeypatch.setattr(vision_service.settings, "VISION_PRIVACY_MODE", "local")
    _sin_tesseract(monkeypatch)
    usados = []

    def crear(p, t=0.2, vision=False):
        usados.append(p)
        return Modelo(messages=iter([AIMessage(json.dumps(BUENA))]))
    monkeypatch.setattr(llm, "proveedores_configurados", lambda orden=None: ["gemini", "ollama"])
    monkeypatch.setattr(llm, "crear_modelo", crear)
    r = vision_service.extraer(imagen_utils.Entrada(imagen=_jpeg()), consentimiento=True)
    assert usados == ["ollama"] and r["metodo"] == "vision:ollama"


def test_modo_externo_sin_autorizacion_no_envia(monkeypatch):
    monkeypatch.setattr(vision_service.settings, "VISION_PRIVACY_MODE", "externo")
    _sin_tesseract(monkeypatch)
    Espia.recibidos = []
    _proveedores(monkeypatch, {"gemini": lambda: Espia(messages=iter([AIMessage(json.dumps(BUENA))]))})
    r = vision_service.extraer(imagen_utils.Entrada(imagen=_jpeg()), consentimiento=False)
    assert Espia.recibidos == [] and r["metodo"] == "manual"


# --- API: subir, confirmar, permisos ------------------------------------------------------

def _subir(h, color="white", consentimiento="true"):
    return client.post("/api/vision/invoices", headers=h, data={"consentimiento": consentimiento},
                       files={"file": ("factura.jpg", _jpeg(color=color), "image/jpeg")})


def test_subir_factura_legible_queda_validada_y_registrada(db, monkeypatch):
    monkeypatch.setattr(vision_service, "anonimizar_imagen", lambda img: (img, []))
    _proveedores(monkeypatch, {"gemini": lambda: Modelo(messages=iter([AIMessage(json.dumps(BUENA))]))})
    u, h = _user(db, "vis_ana")
    r = _subir(h, color="blue")
    assert r.status_code == 200, r.text
    f = r.json()
    assert f["estado"] == "validada" and f["nit"] == "800197268-4" and f["total"] == 35700
    assert f["confianza_campos"]["nit"] >= 0.9 and f["metodo"] == "vision:gemini"
    db.expire_all()
    assert db.query(Auditoria).filter(Auditoria.accion == "subir_imagen", Auditoria.usuario_id == u.id).count() >= 1
    ej = db.query(Ejecucion).filter(Ejecucion.flujo == "vision_factura").order_by(Ejecucion.id.desc()).first()
    assert ej.resultado["estado"] == "validada"
    # la misma imagen otra vez se marca como posible duplicado
    r2 = _subir(h, color="blue")
    assert any("duplicado" in a for a in r2.json()["advertencias"])


def test_confirmar_registra_correcciones_y_controla_acceso(db, monkeypatch):
    monkeypatch.setattr(vision_service, "anonimizar_imagen", lambda img: (img, []))
    mala = {**BUENA, "nit": "800197268-5", "confianza_campos": {**BUENA["confianza_campos"], "nit": 0.6}}
    _proveedores(monkeypatch, {"gemini": lambda: Modelo(messages=iter([AIMessage(json.dumps(mala))]))})
    u, h = _user(db, "vis_luis")
    f = _subir(h, color="green").json()
    assert f["estado"] == "por_confirmar"

    _, otro = _user(db, "vis_intruso")
    assert client.get(f"/api/vision/invoices/{f['id']}", headers=otro).status_code == 404
    assert client.get(f"/api/vision/images/{f['imagen_id']}", headers=otro).status_code == 404
    img = client.get(f"/api/vision/images/{f['imagen_id']}", headers=h)
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"

    body = {"proveedor": "Ferretería El Tornillo SAS", "nit": "800197268-4", "fecha": "2026-10-20", "total": 35700}
    r = client.post(f"/api/vision/invoices/{f['id']}/confirm", headers=h, json=body)
    assert r.status_code == 200 and r.json()["estado"] == "confirmada"
    a = db.query(Auditoria).filter(Auditoria.accion == "confirmar_factura").order_by(Auditoria.id.desc()).first()
    assert a.detalle["correcciones"] == ["nit"]
    assert client.post(f"/api/vision/invoices/{f['id']}/confirm", headers=h, json=body).status_code == 409

    listado = client.get("/api/vision/invoices?estado=confirmada", headers=h).json()
    assert [x["id"] for x in listado] == [f["id"]]
    assert all(x["id"] != f["id"] for x in client.get("/api/vision/invoices", headers=otro).json())


def test_subir_archivo_invalido_o_sin_sesion(db):
    _, h = _user(db, "vis_ana")
    assert client.post("/api/vision/invoices", files={"file": ("a.jpg", _jpeg(), "image/jpeg")}).status_code == 401
    r = client.post("/api/vision/invoices", headers=h, files={"file": ("a.txt", b"hola", "text/plain")})
    assert r.status_code == 400

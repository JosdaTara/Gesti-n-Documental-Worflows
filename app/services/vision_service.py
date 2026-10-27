import json, hashlib, io
from sqlalchemy.orm import Session
from app.models import Factura, Imagen
from app.services import storage
from app.core.config import settings
from app.core.logging import log_execution
from app.schemas import VisionFactura, VisionOut

PROMPT = """Extrae los datos de esta factura y devuelve SOLO JSON valido con esta forma:
{"proveedor":"","fecha":"YYYY-MM-DD","nit":"","moneda":"COP","items":[{"descripcion":"","cantidad":1,"valor":0}],"total":0,"confianza":0.0}
confianza es un numero entre 0 y 1 con tu certeza global del resultado."""

def _gemini_vision(data: bytes, mime: str) -> dict:
    import google.generativeai as genai
    genai.configure(api_key=settings.GEMINI_API_KEY)
    m = genai.GenerativeModel(settings.GEMINI_MODEL)
    r = m.generate_content([PROMPT, {"mime_type": mime, "data": data}],
                           generation_config={"response_mime_type": "application/json", "temperature": 0.1})
    try:
        return json.loads(r.text)
    except Exception:
        return {"proveedor": None, "fecha": None, "nit": None, "items": [], "total": None, "confianza": 0.0}

def _ocr_fallback(data: bytes) -> dict:
    try:
        import pytesseract
        from PIL import Image
        txt = pytesseract.image_to_string(Image.open(io.BytesIO(data)), lang="spa")
    except Exception as e:
        return {"proveedor": None, "fecha": None, "nit": None, "items": [], "total": None, "confianza": 0.0, "detalle": str(e)}
    # Estructurar con LLM
    import google.generativeai as genai
    genai.configure(api_key=settings.GEMINI_API_KEY)
    m = genai.GenerativeModel(settings.GEMINI_MODEL)
    r = m.generate_content("Estructura esta factura en JSON con claves proveedor,fecha,nit,items,total,confianza.\n\n" + txt[:6000],
                           generation_config={"response_mime_type": "application/json", "temperature": 0.2})
    try:
        o = json.loads(r.text); o.setdefault("confianza", 0.6); return o
    except Exception:
        return {"proveedor": None, "items": [], "confianza": 0.0}

def process_invoice(db: Session, data: bytes, mime: str, usuario_id: int) -> VisionOut:
    with log_execution(db, "vision_factura", "web", {"mime": mime, "tamano": len(data)}):
        key, h = storage.save_bytes(data, mime, "img")
        img = Imagen(storage_key=key, mime=mime, tamano=len(data), hash_sha256=h, subido_por=usuario_id)
        db.add(img); db.commit(); db.refresh(img)
        try:
            o = _gemini_vision(data, mime)
        except Exception:
            o = _ocr_fallback(data)
        conf = float(o.get("confianza") or 0.0)
        estado = "validada" if conf >= settings.CONFIDENCE_THRESHOLD else "por_confirmar"
        f = Factura(documento_id=None, proveedor=o.get("proveedor"), nit=o.get("nit"),
                    items=o.get("items") or [], total=o.get("total"), confianza=conf, estado=estado,
                    creado_por=usuario_id)
        # fecha
        from datetime import date
        if o.get("fecha"):
            try: f.fecha = date.fromisoformat(o["fecha"])
            except Exception: pass
        db.add(f); db.commit(); db.refresh(f)
        vf = VisionFactura(proveedor=f.proveedor, fecha=str(f.fecha) if f.fecha else None, nit=f.nit,
                          items=f.items or [], total=f.total, confianza=conf)
        return VisionOut(factura=vf, estado=estado, imagen_id=str(img.id))

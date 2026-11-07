"""Módulo de visión: lectura de facturas y recibos (Componente 2).

Flujo:
  1. Validar y normalizar el archivo (foto de celular o PDF) → `imagen_utils.preparar`.
  2. Guardar el original en Storage privado y registrar quién lo subió y cuándo.
  3. Extraer los datos, en este orden (plan B en cascada):
       a. Modelo multimodal (Gemini → Groq → Ollama) con la imagen anonimizada.
       b. OCR local (Tesseract) + modelo de texto con los datos personales enmascarados.
       c. Registro manual: la factura queda `por_confirmar` con los campos vacíos.
     Un PDF digital no necesita visión: se usa su texto enmascarado.
  4. Normalizar, validar y calcular la confianza por campo → `validacion_factura.evaluar`.
  5. Si la confianza es alta queda `validada`; si no, `por_confirmar` para revisión humana.
"""
import base64
from datetime import datetime, timezone

from fastapi import HTTPException
from langchain_core.messages import HumanMessage
from sqlalchemy.orm import Session

from app.core.audit import auditar
from app.core.config import settings
from app.core.logging import con_reintentos, log_execution
from app.models import Factura, Imagen
from app.services import llm, storage
from app.services.imagen_utils import Entrada, preparar
from app.services.privacidad import TesseractNoDisponible, anonimizar_imagen, enmascarar_texto, ocr_texto
from app.services.validacion_factura import evaluar, normalizar_fecha, normalizar_items, normalizar_nit, \
    normalizar_numero, requiere_revision

PROMPT = """Eres un sistema que lee facturas y recibos de Colombia. Extrae los datos del EMISOR (quien vende)
y responde SOLO con este JSON:
{"proveedor": "razón social del emisor", "nit": "NIT del emisor con dígito de verificación, ej. 900123456-7",
 "fecha": "YYYY-MM-DD (fecha de emisión)", "moneda": "COP",
 "items": [{"descripcion": "", "cantidad": 1, "valor": 0}], "total": 0,
 "confianza_campos": {"proveedor": 0.0, "nit": 0.0, "fecha": 0.0, "total": 0.0, "items": 0.0}}
Reglas: números sin símbolos ni separadores de miles; null si un dato no aparece o no es legible;
"valor" es el precio unitario; confianza_campos es tu certeza de 0 a 1 para cada campo.
Algunos datos personales pueden aparecer tapados o como [CORREO], [TELEFONO] o [CEDULA]: ignóralos."""


# --- extracción -------------------------------------------------------------------

def _texto_proveedores(modo: str) -> list[str]:
    todos = llm.proveedores_configurados()
    return [p for p in todos if p in llm.LOCALES] if modo == "local" else todos


def _desde_texto(texto: str, modo: str, usuario_id: int | None, accion: str) -> tuple[dict, str, list[str]]:
    enmascarado, tipos = enmascarar_texto(texto)
    crudo, proveedor = llm.invocar_json(PROMPT + "\n\nTexto de la factura:\n" + enmascarado[:8000],
                                        accion=accion, usuario_id=usuario_id,
                                        proveedores=_texto_proveedores(modo))
    return crudo, proveedor, tipos


def _desde_imagen(imagen: bytes, proveedores: list[str], usuario_id: int | None) -> tuple[dict, str]:
    b64 = base64.b64encode(imagen).decode()
    mensaje = HumanMessage(content=[{"type": "text", "text": PROMPT},
                                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}])
    crudo, proveedor, fallos = llm.con_fallback(
        lambda m: llm.extraer_json(llm.texto_de(m.invoke([mensaje]))),
        temperatura=0.1, proveedores=proveedores, vision=True)
    auditar("ia:vision_factura", usuario_id=usuario_id, entidad="llm", detalle={
        "proveedor": proveedor, "modelo": llm.nombre_modelo(proveedor, vision=True), "fallos_previos": fallos,
        "salida": {k: crudo.get(k) for k in ("proveedor", "nit", "fecha", "total")}})
    return crudo, proveedor


def extraer(entrada: Entrada, consentimiento: bool, usuario_id: int | None = None) -> dict:
    """Devuelve {crudo, metodo, datos_enmascarados, advertencias, fallos}."""
    modo = settings.VISION_PRIVACY_MODE
    advertencias: list[str] = []
    enmascarados: set[str] = set()
    fallos: list[str] = []

    if entrada.texto:  # PDF digital
        try:
            crudo, prov, tipos = _desde_texto(entrada.texto, modo, usuario_id, "vision_factura_texto")
            return {"crudo": crudo, "metodo": f"texto:{prov}", "datos_enmascarados": tipos,
                    "advertencias": advertencias, "fallos": fallos}
        except (llm.LLMNoDisponible, ValueError) as e:
            fallos.append(str(e))
    else:
        # a) modelo multimodal
        proveedores = llm.proveedores_configurados(settings.VISION_PROVIDERS)
        locales = [p for p in proveedores if p in llm.LOCALES]
        imagen = entrada.imagen
        if modo == "local":
            proveedores = locales
        elif modo == "anonimizado" and len(locales) < len(proveedores):
            try:
                imagen, tipos = anonimizar_imagen(entrada.imagen)
                enmascarados.update(tipos)
            except TesseractNoDisponible:
                proveedores = locales
                advertencias.append("No se pudo anonimizar la imagen (falta Tesseract); por protección de datos "
                                    "no se envió a servicios externos.")
        elif modo == "externo" and not consentimiento:
            proveedores = locales
            advertencias.append("Sin la autorización de tratamiento de datos la imagen no se envía a servicios externos.")
        try:
            crudo, prov = _desde_imagen(imagen, proveedores, usuario_id)
            return {"crudo": crudo, "metodo": f"vision:{prov}", "datos_enmascarados": sorted(enmascarados),
                    "advertencias": advertencias, "fallos": fallos}
        except llm.LLMNoDisponible as e:
            fallos.extend(e.fallos or [str(e)])

        # b) plan B: OCR local + modelo de texto
        try:
            crudo, prov, tipos = _desde_texto(ocr_texto(entrada.imagen), modo, usuario_id, "vision_factura_ocr")
            advertencias.append("Se usó el plan B (OCR): revise los datos.")
            return {"crudo": crudo, "metodo": f"ocr+{prov}", "datos_enmascarados": sorted(enmascarados | set(tipos)),
                    "advertencias": advertencias, "fallos": fallos}
        except (TesseractNoDisponible, llm.LLMNoDisponible, ValueError) as e:
            fallos.append(f"ocr: {e}")

    # c) último recurso: registro manual
    advertencias.append("No fue posible leer la factura automáticamente en este momento. "
                        "Puede diligenciar los datos a mano o intentarlo más tarde.")
    return {"crudo": {}, "metodo": "manual", "datos_enmascarados": sorted(enmascarados),
            "advertencias": advertencias, "fallos": fallos}


# --- casos de uso -----------------------------------------------------------------

def factura_out(f: Factura) -> dict:
    from sqlalchemy.orm import object_session
    from app.models import Alerta
    s = object_session(f)
    alertas = [{"id": a.id, "tipo": a.tipo, "severidad": a.severidad, "mensaje": a.mensaje, "estado": a.estado}
               for a in s.query(Alerta).filter(Alerta.entidad == "factura", Alerta.entidad_id == str(f.id))
               .order_by(Alerta.id).all()] if s else []
    return {"alertas": alertas, "procesada": bool(f.procesada_en),"id": str(f.id), "imagen_id": str(f.imagen_id) if f.imagen_id else None,
            "proveedor": f.proveedor, "fecha": f.fecha.isoformat() if f.fecha else None, "nit": f.nit,
            "moneda": f.moneda or "COP", "items": f.items or [], "total": f.total,
            "confianza": f.confianza or 0.0, "confianza_campos": f.confianza_campos or {},
            "estado": f.estado, "metodo": f.metodo, "origen": f.origen, "advertencias": f.advertencias or [],
            "creado_en": f.creado_en.isoformat() if f.creado_en else None,
            "revisado_en": f.revisado_en.isoformat() if f.revisado_en else None}


def _jsonable(ev: dict) -> dict:
    return {"proveedor": ev["proveedor"], "fecha": ev["fecha"].isoformat() if ev["fecha"] else None,
            "nit": ev["nit"], "total": ev["total"], "items": ev["items"], "moneda": ev["moneda"]}


def process_invoice(db: Session, data: bytes, mime: str, usuario_id: int, consentimiento: bool = False,
                    canal: str = "web") -> dict:
    if len(data) > settings.VISION_MAX_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"La imagen supera los {settings.VISION_MAX_MB} MB.")
    if not data:
        raise HTTPException(status_code=400, detail="El archivo está vacío.")
    with log_execution(db, "vision_factura", canal, {"mime": mime, "tamano": len(data), "usuario_id": usuario_id,
                                                     "consentimiento": consentimiento}) as rec:
        entrada = preparar(data, mime)
        key, h = con_reintentos(storage.save_bytes)(data, mime or "application/octet-stream", "img")
        previa = db.query(Imagen).filter(Imagen.hash_sha256 == h).first()
        img = Imagen(storage_key=key, mime=mime, tamano=len(data), hash_sha256=h, subido_por=usuario_id)
        db.add(img); db.commit(); db.refresh(img)
        auditar("subir_imagen", usuario_id=usuario_id, entidad="imagen", entidad_id=str(img.id), detalle={
            "mime": mime, "tamano": len(data), "sha256": h, "consentimiento": consentimiento,
            "modo_privacidad": settings.VISION_PRIVACY_MODE})

        r = extraer(entrada, consentimiento, usuario_id)
        ev = evaluar(r["crudo"])
        advertencias = r["advertencias"] + ev["advertencias"]
        if previa:
            advertencias.insert(0, "Esta misma imagen ya se había subido antes: verifique que no sea un duplicado.")
        if r["datos_enmascarados"]:
            advertencias.append("Datos personales protegidos antes de usar la IA: " + ", ".join(r["datos_enmascarados"]) + ".")
        revisar = r["metodo"] == "manual" or requiere_revision(ev, settings.CONFIDENCE_THRESHOLD)

        f = Factura(imagen_id=img.id, proveedor=ev["proveedor"], fecha=ev["fecha"], nit=ev["nit"],
                    moneda=ev["moneda"], items=ev["items"], total=ev["total"], confianza=ev["confianza"],
                    confianza_campos=ev["confianza_campos"], metodo=r["metodo"], origen=entrada.origen,
                    advertencias=advertencias, datos_ia=_jsonable(ev),
                    estado="por_confirmar" if revisar else "validada", creado_por=usuario_id)
        db.add(f); db.commit(); db.refresh(f)
        rec.resultado = {"factura_id": str(f.id), "estado": f.estado, "metodo": f.metodo,
                         "confianza": f.confianza, "fallos": r["fallos"]}
    if f.estado == "validada":   # alta confianza: la visión dispara la automatización sin pasos manuales
        from app.services.automatizacion_facturas import disparar
        disparar(db, str(f.id), "vision")
        db.refresh(f)
    return factura_out(f)


def _puede_ver(f_o_img_dueno: int | None, usuario: dict) -> bool:
    return usuario["rol"] == "admin" or f_o_img_dueno == usuario["id"]


def _puede_ver_factura(f: Factura, usuario: dict) -> bool:
    from app.core.security import tiene_permiso
    return tiene_permiso(usuario, "ver_todas_facturas") or f.creado_por == usuario["id"]


def obtener(db: Session, factura_id: str, usuario: dict) -> Factura:
    import uuid
    try:
        f = db.query(Factura).filter(Factura.id == uuid.UUID(str(factura_id))).first()
    except ValueError:
        f = None
    if not f or not _puede_ver_factura(f, usuario):
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    return f


def listar(db: Session, usuario: dict, estado: str | None = None, limite: int = 50) -> list[dict]:
    from app.core.security import tiene_permiso
    q = db.query(Factura)
    if not tiene_permiso(usuario, "ver_todas_facturas"):
        q = q.filter(Factura.creado_por == usuario["id"])
    if estado:
        q = q.filter(Factura.estado == estado)
    return [factura_out(f) for f in q.order_by(Factura.creado_en.desc()).limit(limite).all()]


def confirmar(db: Session, factura_id: str, cambios: dict, usuario: dict) -> dict:
    """Revisión humana: el usuario corrige los campos y confirma la factura."""
    with log_execution(db, "confirmar_factura", "web", {"factura_id": factura_id, "usuario_id": usuario["id"]}) as rec:
        f = obtener(db, factura_id, usuario)
        if not _puede_ver(f.creado_por, usuario):
            raise HTTPException(status_code=403, detail="Solo quien subió la factura o un administrador puede confirmarla.")
        if f.estado == "confirmada":
            raise HTTPException(status_code=409, detail="La factura ya fue confirmada.")
        fecha = normalizar_fecha(cambios.get("fecha")) if cambios.get("fecha") else None
        if cambios.get("fecha") and not fecha:
            raise HTTPException(status_code=400, detail="Fecha inválida (use AAAA-MM-DD).")
        nit, estado_nit = normalizar_nit(cambios.get("nit"))
        total = normalizar_numero(cambios.get("total"))
        if not cambios.get("proveedor") or not total or total <= 0:
            raise HTTPException(status_code=400, detail="Proveedor y total (mayor que cero) son obligatorios.")
        nuevos = {"proveedor": str(cambios["proveedor"]).strip()[:255], "fecha": fecha, "nit": nit,
                  "total": total, "moneda": str(cambios.get("moneda") or f.moneda or "COP").upper()[:8],
                  "items": normalizar_items(cambios.get("items")) if "items" in cambios else (f.items or [])}
        ia = f.datos_ia or {}
        comparables = {**nuevos, "fecha": fecha.isoformat() if fecha else None}
        correcciones = [c for c in ("proveedor", "fecha", "nit", "total") if ia.get(c) != comparables[c]]
        for k, v in nuevos.items():
            setattr(f, k, v)
        f.estado = "confirmada"; f.revisado_por = usuario["id"]; f.revisado_en = datetime.now(timezone.utc)
        if estado_nit == "dv_invalido":
            f.advertencias = (f.advertencias or []) + ["Se confirmó un NIT con dígito de verificación inválido."]
        db.commit(); db.refresh(f)
        auditar("confirmar_factura", usuario_id=usuario["id"], entidad="factura", entidad_id=str(f.id),
                detalle={"correcciones": correcciones, "metodo": f.metodo, "confianza_ia": f.confianza})
        rec.resultado = {"correcciones": correcciones}
    from app.services.automatizacion_facturas import disparar
    disparar(db, str(f.id), "confirmacion")
    db.refresh(f)
    return factura_out(f)


def imagen(db: Session, imagen_id: str, usuario: dict) -> tuple[bytes, str]:
    """Devuelve la imagen original solo a quien la subió o a un administrador."""
    import uuid
    try:
        img = db.query(Imagen).filter(Imagen.id == uuid.UUID(str(imagen_id))).first()
    except ValueError:
        img = None
    if not img or not _puede_ver(img.subido_por, usuario):
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    auditar("ver_imagen", usuario_id=usuario["id"], entidad="imagen", entidad_id=str(img.id))
    return con_reintentos(storage.read_bytes)(img.storage_key), img.mime or "image/jpeg"

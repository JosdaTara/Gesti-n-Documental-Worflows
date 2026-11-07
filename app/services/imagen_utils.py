"""Preparación de imágenes y PDF antes de enviarlos al modelo de visión.

Las fotos de celular llegan rotadas (EXIF), muy grandes y en distintos formatos. Aquí se
normalizan a JPEG, orientación correcta y máximo `LADO_MAX` px por lado.
"""
import io
from dataclasses import dataclass

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

LADO_MAX = 2000
MIMES_IMAGEN = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
MIME_PDF = "application/pdf"


@dataclass
class Entrada:
    """Lo que se le entrega al extractor: una imagen JPEG o el texto de un PDF digital."""
    imagen: bytes | None = None
    texto: str | None = None
    origen: str = "foto"   # foto | pdf_digital | pdf_escaneado


def _normalizar(img: Image.Image) -> bytes:
    img = ImageOps.exif_transpose(img)          # corrige fotos giradas del celular
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((LADO_MAX, LADO_MAX))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88)
    return out.getvalue()


def preparar(data: bytes, mime: str) -> Entrada:
    mime = (mime or "").lower()
    if mime == MIME_PDF or data[:4] == b"%PDF":
        return _desde_pdf(data)
    if mime and mime not in MIMES_IMAGEN:
        raise HTTPException(status_code=400, detail="Formato no soportado. Sube una foto JPG, PNG o WEBP, o un PDF.")
    try:
        return Entrada(imagen=_normalizar(Image.open(io.BytesIO(data))), origen="foto")
    except UnidentifiedImageError:
        raise HTTPException(status_code=400, detail="El archivo no es una imagen válida "
                                                    "(si es HEIC del iPhone, compártela como JPG).")


def _desde_pdf(data: bytes) -> Entrada:
    from pypdf import PdfReader
    try:
        pagina = PdfReader(io.BytesIO(data)).pages[0]
    except Exception:
        raise HTTPException(status_code=400, detail="No se pudo leer el PDF.")
    texto = (pagina.extract_text() or "").strip()
    if len(texto) >= 40:                          # PDF generado por software: ya trae texto
        return Entrada(texto=texto[:8000], origen="pdf_digital")
    imagenes = list(getattr(pagina, "images", []) or [])
    if not imagenes:
        raise HTTPException(status_code=400, detail="El PDF no tiene texto ni imágenes legibles.")
    mayor = max(imagenes, key=lambda i: len(i.data))  # PDF escaneado: la imagen de la página
    return Entrada(imagen=_normalizar(Image.open(io.BytesIO(mayor.data))), origen="pdf_escaneado")

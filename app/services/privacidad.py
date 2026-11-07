"""Protección de datos personales (Ley 1581 de 2012) en el módulo de visión.

Antes de enviar una factura a un servicio de IA externo se ocultan los datos personales
de personas naturales que suelen aparecer en ella: correos, teléfonos y números de cédula.
El NIT del proveedor NO se oculta porque identifica a la empresa y es un dato necesario
para registrar la compra.

- En texto (OCR o PDF digital) se reemplazan por etiquetas: [CORREO], [TELEFONO], [CEDULA].
- En imágenes se localizan con Tesseract y se tapan con un rectángulo negro.
"""
import io
import re

from PIL import Image, ImageDraw

PATRONES = {
    "correo": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    # celulares (3xx xxx xxxx) y fijos con indicativo (60x xxx xxxx), con o sin +57
    "telefono": re.compile(r"(?:\+?57[\s-]?)?(?:3\d{2}|60\d)[\s.-]?\d{3}[\s.-]?\d{4}\b"),
    # cédula precedida de C.C. / CC / cédula
    "cedula": re.compile(r"\b(?:C\.?\s?C\.?|c[eé]dula(?: de ciudadan[ií]a)?)(?:\s*(?:No\.?|N°|#))?[\s:]*([\d.\s]{6,15}\d)", re.I),
}
ETIQUETAS = {"correo": "[CORREO]", "telefono": "[TELEFONO]", "cedula": "[CEDULA]"}


class TesseractNoDisponible(RuntimeError):
    reintentable = False


def enmascarar_texto(texto: str) -> tuple[str, list[str]]:
    """Devuelve el texto con los datos personales reemplazados y los tipos encontrados."""
    encontrados: list[str] = []
    for tipo, patron in PATRONES.items():
        if patron.search(texto):
            encontrados.append(tipo)
            texto = patron.sub(ETIQUETAS[tipo], texto)
    return texto, encontrados


def ocr_disponible() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_texto(imagen: bytes) -> str:
    if not ocr_disponible():
        raise TesseractNoDisponible("Tesseract no está instalado en el servidor.")
    import pytesseract
    return pytesseract.image_to_string(Image.open(io.BytesIO(imagen)), lang="spa")


def anonimizar_imagen(imagen: bytes) -> tuple[bytes, list[str]]:
    """Tapa en la imagen las palabras que contienen datos personales (requiere Tesseract)."""
    if not ocr_disponible():
        raise TesseractNoDisponible("Tesseract no está instalado: no se puede anonimizar la imagen.")
    import pytesseract
    img = Image.open(io.BytesIO(imagen)).convert("RGB")
    d = pytesseract.image_to_data(img, lang="spa", output_type=pytesseract.Output.DICT)

    # Se agrupan las palabras por línea para detectar patrones que ocupan varias palabras.
    lineas: dict[tuple, list[int]] = {}
    for i, palabra in enumerate(d["text"]):
        if palabra.strip():
            lineas.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(i)

    dibujo = ImageDraw.Draw(img)
    encontrados: set[str] = set()
    for idx in lineas.values():
        # posición (inicio, fin) de cada palabra dentro del texto de la línea
        partes, pos, texto = [], 0, ""
        for i in idx:
            if texto:
                texto += " "; pos += 1
            partes.append((pos, pos + len(d["text"][i]), i))
            texto += d["text"][i]; pos += len(d["text"][i])
        for tipo, patron in PATRONES.items():
            for m in patron.finditer(texto):
                ini, fin = m.span(1) if m.groups() else m.span()
                for a, b, i in partes:
                    if a < fin and b > ini:
                        x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
                        dibujo.rectangle([x - 2, y - 2, x + w + 2, y + h + 2], fill="black")
                        encontrados.add(tipo)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88)
    return out.getvalue(), sorted(encontrados)

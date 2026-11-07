"""Normalización, validación y confianza por campo de una factura extraída por la IA.

La confianza de cada campo combina:
  1. la certeza que reporta el modelo para ese campo, y
  2. reglas de negocio verificables (formato de fecha, dígito de verificación del NIT,
     total positivo y coherente con la suma de los ítems).
Así, si el modelo dice estar seguro de un NIT cuyo dígito de verificación no cuadra,
la confianza baja y la factura se envía a revisión humana.
"""
import re
from datetime import date, timedelta

CAMPOS_REQUERIDOS = ("proveedor", "fecha", "nit", "total")
PESOS_NIT = (3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71)


def digito_verificacion(nit: str) -> int:
    """Dígito de verificación de la DIAN para un NIT (sin el DV)."""
    digitos = [int(c) for c in reversed(nit)]
    r = sum(d * p for d, p in zip(digitos, PESOS_NIT)) % 11
    return r if r in (0, 1) else 11 - r


def normalizar_nit(valor) -> tuple[str | None, str]:
    """Devuelve (nit_normalizado 'NNNNNNNNN-D' o 'NNNNNNNNN', estado: valido|sin_dv|dv_invalido|invalido|vacio)."""
    if not valor:
        return None, "vacio"
    s = re.sub(r"[^\d-]", "", str(valor)).strip("-")      # quita puntos, espacios y letras
    con_dv = re.fullmatch(r"(\d{6,10})-(\d)", s)
    if con_dv:
        base, dv = con_dv.groups()
        return f"{base}-{dv}", "valido" if digito_verificacion(base) == int(dv) else "dv_invalido"
    if re.fullmatch(r"\d{6,10}", s):
        # 9001234567 puede ser NIT 900123456 con DV 7 pegado: se acepta solo si el DV cuadra
        if len(s) == 10 and digito_verificacion(s[:9]) == int(s[9]):
            return f"{s[:9]}-{s[9]}", "valido"
        return s, "sin_dv"
    return str(valor)[:40], "invalido"


def normalizar_numero(valor) -> float | None:
    """'$1.234.567,50' -> 1234567.5 ; '1,234,567.50' -> 1234567.5"""
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    s = re.sub(r"[^\d.,-]", "", str(valor))
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if len(s.split(",")[-1]) <= 2 else s.replace(",", "")
    elif s.count(".") > 1 or (s.count(".") == 1 and len(s.split(".")[-1]) == 3):
        s = s.replace(".", "")                # separador de miles colombiano
    try:
        return float(s)
    except ValueError:
        return None


def normalizar_fecha(valor) -> date | None:
    if not valor:
        return None
    s = str(valor).strip()
    for patron, orden in ((r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", "ymd"), (r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", "dmy")):
        m = re.search(patron, s)
        if m:
            a, b, c = (int(x) for x in m.groups())
            y, mo, d = (a, b, c) if orden == "ymd" else (c, b, a)
            try:
                return date(y, mo, d)
            except ValueError:
                return None
    return None


def normalizar_items(items) -> list[dict]:
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        out.append({"descripcion": str(it.get("descripcion") or "")[:200],
                    "cantidad": normalizar_numero(it.get("cantidad")) or 1,
                    "valor": normalizar_numero(it.get("valor")) or 0.0})
    return out


def evaluar(crudo: dict) -> dict:
    """Normaliza la salida del modelo y calcula la confianza por campo y global."""
    conf_modelo = crudo.get("confianza_campos") or {}
    general = crudo.get("confianza")

    def cm(campo):
        v = conf_modelo.get(campo, general if isinstance(general, (int, float)) else 0.7)
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.5

    advertencias: list[str] = []
    proveedor = (str(crudo.get("proveedor")).strip()[:255] or None) if crudo.get("proveedor") else None
    fecha = normalizar_fecha(crudo.get("fecha"))
    nit, estado_nit = normalizar_nit(crudo.get("nit"))
    total = normalizar_numero(crudo.get("total"))
    items = normalizar_items(crudo.get("items"))
    moneda = (str(crudo.get("moneda") or "COP").upper()[:8])

    v_prov = 1.0 if proveedor and len(proveedor) >= 3 else 0.0
    if fecha is None:
        v_fecha = 0.3 if crudo.get("fecha") else 0.0
    elif date(2000, 1, 1) <= fecha <= date.today() + timedelta(days=1):
        v_fecha = 1.0
    else:
        v_fecha = 0.3; advertencias.append("La fecha está fuera de un rango razonable.")
    v_nit = {"valido": 1.0, "sin_dv": 0.8, "dv_invalido": 0.3, "invalido": 0.2, "vacio": 0.0}[estado_nit]
    if estado_nit == "dv_invalido":
        advertencias.append("El dígito de verificación del NIT no coincide: revíselo.")
    v_total = 1.0 if total and total > 0 else 0.0
    suma = sum(i["cantidad"] * i["valor"] for i in items)
    if items and total and suma > 0:
        # el total puede incluir IVA (19 %) o impoconsumo (8 %)
        if not any(abs(total - suma * f) <= max(1.0, 0.02 * total) for f in (1, 1.19, 1.08)):
            v_total = min(v_total, 0.6)
            advertencias.append("El total no coincide con la suma de los ítems.")

    campos = {"proveedor": round(cm("proveedor") * v_prov, 2), "fecha": round(cm("fecha") * v_fecha, 2),
              "nit": round(cm("nit") * v_nit, 2), "total": round(cm("total") * v_total, 2),
              "items": round(cm("items") * (1.0 if items else 0.5), 2)}
    global_ = round(sum(campos[c] for c in CAMPOS_REQUERIDOS) / len(CAMPOS_REQUERIDOS), 2)
    faltantes = [c for c in CAMPOS_REQUERIDOS if campos[c] == 0]
    if faltantes:
        advertencias.append("No se pudo leer: " + ", ".join(faltantes) + ".")
    return {"proveedor": proveedor, "fecha": fecha, "nit": nit, "total": total, "items": items,
            "moneda": moneda, "confianza_campos": campos, "confianza": global_,
            "advertencias": advertencias, "estado_nit": estado_nit}


def requiere_revision(evaluado: dict, umbral: float) -> bool:
    campos = evaluado["confianza_campos"]
    return evaluado["confianza"] < umbral or any(campos[c] < 0.5 for c in CAMPOS_REQUERIDOS)

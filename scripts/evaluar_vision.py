"""Evaluación del módulo de visión con fotos reales de facturas (requisito: ≥ 20 imágenes).

Uso:
  1. Copie las fotos (JPG/PNG/WEBP o PDF) en una carpeta, p. ej. `evaluacion/fotos/`.
  2. Genere la plantilla de respuestas correctas y llénela a mano (se abre en Excel):
         python scripts/evaluar_vision.py plantilla evaluacion/fotos
     Crea `evaluacion/fotos/verdad.csv` con columnas: archivo, proveedor, nit, fecha, total.
  3. Ejecute la evaluación (usa la misma configuración de IA y privacidad del .env):
         python scripts/evaluar_vision.py evaluar evaluacion/fotos --consentimiento
     Genera en `evaluacion/fotos/resultados/`:
       - resultados.csv  → una fila por imagen y campo (esperado, obtenido, acierto, confianza)
       - reporte.md      → % de aciertos por campo, global, por método, calibración de la confianza
                            y cuántas facturas se habrían validado solas con error.

  Alternativa con datos de uso real (facturas ya confirmadas por los usuarios en la app):
         python scripts/evaluar_vision.py bd
     Compara lo que leyó la IA (`datos_ia`) con lo que el usuario confirmó.
"""
import argparse
import csv
import difflib
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

CAMPOS = ("proveedor", "nit", "fecha", "total")
EXTENSIONES = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
MIMES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
         ".pdf": "application/pdf"}
SUFIJOS_EMPRESA = r"\b(s\s?a\s?s|s\s?a|ltda|limitada|e\s?u|s\s?en\s?c|y\s?cia|cia)\b"


# --- comparación de campos ------------------------------------------------------------

def _texto_base(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(SUFIJOS_EMPRESA, " ", s)
    return re.sub(r"\s+", " ", s).strip()


def comparar(campo: str, esperado, obtenido) -> bool | None:
    """True/False si acierta; None si no hay valor esperado (el campo no se evalúa)."""
    from app.services.validacion_factura import normalizar_fecha, normalizar_nit, normalizar_numero
    if esperado in (None, ""):
        return None
    if obtenido in (None, ""):
        return False
    if campo == "proveedor":
        a, b = _texto_base(esperado), _texto_base(obtenido)
        return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.85
    if campo == "nit":
        e, _ = normalizar_nit(esperado)
        o, _ = normalizar_nit(obtenido)
        if not e or not o:
            return False
        # si la respuesta correcta no trae DV se compara solo la base
        return e == o if "-" in e else e == o.split("-")[0]
    if campo == "fecha":
        e = normalizar_fecha(esperado)
        o = normalizar_fecha(obtenido) if not hasattr(obtenido, "isoformat") else obtenido
        return e is not None and e == o
    if campo == "total":
        e, o = normalizar_numero(esperado), normalizar_numero(obtenido)
        return e is not None and o is not None and abs(e - o) <= 1.0   # al peso (tolera solo redondeo)
    raise ValueError(campo)


# --- resultados y reporte ---------------------------------------------------------------

@dataclass
class Resultado:
    archivo: str
    metodo: str = ""
    estado: str = ""
    confianza: float = 0.0
    segundos: float = 0.0
    error: str = ""
    campos: dict = field(default_factory=dict)   # campo -> {esperado, obtenido, acierto, confianza}


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f} %" if d else "—"


def reporte(resultados: list[Resultado], umbral: float, titulo: str, nota: str = "") -> str:
    lineas = [f"# {titulo}", "", f"Fecha: {datetime.now():%Y-%m-%d %H:%M} · Imágenes: {len(resultados)} · "
              f"Umbral de confianza: {umbral}", ""]
    if nota:
        lineas += [nota, ""]
    if len(resultados) < 20:
        lineas += [f"> ⚠ Se evaluaron {len(resultados)} imágenes; la guía exige al menos 20.", ""]

    lineas += ["## Aciertos por campo", "", "| Campo | Evaluados | Aciertos | % acierto |", "|---|--:|--:|--:|"]
    tot_n = tot_ok = 0
    for c in CAMPOS:
        vals = [r.campos[c]["acierto"] for r in resultados if c in r.campos and r.campos[c]["acierto"] is not None]
        ok = sum(vals); tot_n += len(vals); tot_ok += ok
        lineas.append(f"| {c} | {len(vals)} | {ok} | {_pct(ok, len(vals))} |")
    lineas.append(f"| **global** | **{tot_n}** | **{tot_ok}** | **{_pct(tot_ok, tot_n)}** |")

    completas = [r for r in resultados if r.campos and all(v["acierto"] is not False for v in r.campos.values())
                 and any(v["acierto"] for v in r.campos.values())]
    lineas += ["", f"Facturas con **todos** los campos correctos: {len(completas)} de {len(resultados)} "
                   f"({_pct(len(completas), len(resultados))}).", ""]

    # calibración: ¿la confianza separa bien aciertos de errores?
    alta = [v for r in resultados for v in r.campos.values() if v["acierto"] is not None and (v["confianza"] or 0) >= umbral]
    baja = [v for r in resultados for v in r.campos.values() if v["acierto"] is not None and (v["confianza"] or 0) < umbral]
    lineas += ["## ¿La confianza es confiable?", "",
               "| Confianza del campo | Campos | % acierto |", "|---|--:|--:|",
               f"| ≥ {umbral} (se aceptaría sin revisión) | {len(alta)} | {_pct(sum(v['acierto'] for v in alta), len(alta))} |",
               f"| < {umbral} (se envía a revisión humana) | {len(baja)} | {_pct(sum(v['acierto'] for v in baja), len(baja))} |", ""]

    validadas = [r for r in resultados if r.estado == "validada"]
    malas = [r for r in validadas if any(v["acierto"] is False for v in r.campos.values())]
    lineas += ["## Decisión automática", "",
               f"- Validadas automáticamente (sin revisión): {len(validadas)} de {len(resultados)} ({_pct(len(validadas), len(resultados))}).",
               f"- **Validadas con algún error** (el riesgo real): {len(malas)}"
               + (f" → {', '.join(r.archivo for r in malas)}" if malas else "") + ".",
               f"- Enviadas a revisión humana: {sum(r.estado == 'por_confirmar' for r in resultados)}.", ""]

    metodos: dict[str, int] = {}
    for r in resultados:
        metodos[r.metodo or "error"] = metodos.get(r.metodo or "error", 0) + 1
    tiempos = [r.segundos for r in resultados if r.segundos]
    lineas += ["## Métodos y tiempos", "", "| Método | Imágenes |", "|---|--:|"]
    lineas += [f"| {m} | {n} |" for m, n in sorted(metodos.items(), key=lambda x: -x[1])]
    if tiempos:
        lineas += ["", f"Tiempo promedio por imagen: {sum(tiempos) / len(tiempos):.1f} s (máx. {max(tiempos):.1f} s)."]

    errores = [(r.archivo, c, v) for r in resultados for c, v in r.campos.items() if v["acierto"] is False]
    if errores:
        lineas += ["", "## Errores encontrados", "", "| Imagen | Campo | Esperado | Obtenido | Confianza |", "|---|---|---|---|--:|"]
        lineas += [f"| {a} | {c} | {v['esperado']} | {v['obtenido'] if v['obtenido'] not in (None, '') else '—'} | {v['confianza']} |"
                   for a, c, v in errores]
    fallidas = [r for r in resultados if r.error]
    if fallidas:
        lineas += ["", "## Imágenes que no se pudieron procesar", ""] + [f"- {r.archivo}: {r.error}" for r in fallidas]
    return "\n".join(lineas) + "\n"


def guardar(resultados: list[Resultado], salida: Path, umbral: float, titulo: str, nota: str = "") -> Path:
    salida.mkdir(parents=True, exist_ok=True)
    with open(salida / "resultados.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["archivo", "campo", "esperado", "obtenido", "acierto", "confianza_campo", "metodo", "estado",
                    "confianza_global", "segundos", "error"])
        for r in resultados:
            for c in CAMPOS:
                v = r.campos.get(c, {})
                w.writerow([r.archivo, c, v.get("esperado", ""), v.get("obtenido", ""),
                            {True: "si", False: "no", None: "n/a"}[v.get("acierto")], v.get("confianza", ""),
                            r.metodo, r.estado, r.confianza, f"{r.segundos:.2f}", r.error])
    (salida / "reporte.md").write_text(reporte(resultados, umbral, titulo, nota), encoding="utf-8")
    return salida / "reporte.md"


# --- modos -------------------------------------------------------------------------------

def imagenes(carpeta: Path) -> list[Path]:
    return sorted(p for p in carpeta.iterdir() if p.suffix.lower() in EXTENSIONES)


def cmd_plantilla(carpeta: Path) -> None:
    destino = carpeta / "verdad.csv"
    if destino.exists():
        sys.exit(f"Ya existe {destino}; bórrelo si quiere regenerarlo.")
    fotos = imagenes(carpeta)
    with open(destino, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["archivo", "proveedor", "nit", "fecha", "total"])
        for p in fotos:
            w.writerow([p.name, "", "", "", ""])
    print(f"Plantilla creada con {len(fotos)} imágenes: {destino}\n"
          "Llénela con los datos reales de cada factura (fecha AAAA-MM-DD, total sin puntos) y luego ejecute 'evaluar'.")


def leer_verdad(carpeta: Path) -> dict[str, dict]:
    ruta = carpeta / "verdad.csv"
    if not ruta.exists():
        sys.exit(f"No existe {ruta}. Genérelo con: python scripts/evaluar_vision.py plantilla {carpeta}")
    with open(ruta, newline="", encoding="utf-8-sig") as fh:
        muestra = fh.read(2048); fh.seek(0)
        dialecto = csv.Sniffer().sniff(muestra, delimiters=",;")   # Excel en español guarda con ';'
        return {fila["archivo"].strip(): fila for fila in csv.DictReader(fh, dialect=dialecto) if fila.get("archivo")}


def evaluar_imagen(ruta: Path, esperado: dict, consentimiento: bool, extraer=None) -> Resultado:
    from app.core.config import settings
    from app.services.imagen_utils import preparar
    from app.services.validacion_factura import evaluar, requiere_revision
    from app.services.vision_service import extraer as extraer_real
    extraer = extraer or extraer_real
    r = Resultado(archivo=ruta.name)
    t0 = time.monotonic()
    try:
        entrada = preparar(ruta.read_bytes(), MIMES.get(ruta.suffix.lower(), ""))
        x = extraer(entrada, consentimiento)
        ev = evaluar(x["crudo"])
        r.metodo = x["metodo"]
        r.confianza = ev["confianza"]
        r.estado = "por_confirmar" if x["metodo"] == "manual" or requiere_revision(ev, settings.CONFIDENCE_THRESHOLD) else "validada"
        for c in CAMPOS:
            obtenido = ev[c].isoformat() if c == "fecha" and ev[c] else ev[c]
            r.campos[c] = {"esperado": (esperado.get(c) or "").strip(), "obtenido": obtenido,
                           "acierto": comparar(c, (esperado.get(c) or "").strip(), obtenido),
                           "confianza": ev["confianza_campos"].get(c)}
    except Exception as e:   # una imagen ilegible no detiene la evaluación
        r.error = getattr(e, "detail", None) or f"{type(e).__name__}: {e}"
        for c in CAMPOS:
            r.campos[c] = {"esperado": (esperado.get(c) or "").strip(), "obtenido": None,
                           "acierto": comparar(c, (esperado.get(c) or "").strip(), None), "confianza": 0.0}
    r.segundos = time.monotonic() - t0
    return r


def cmd_evaluar(carpeta: Path, consentimiento: bool, pausa: float, limite: int | None, extraer=None) -> Path:
    from app.core.config import settings
    from app.services.llm import proveedores_configurados
    verdad = leer_verdad(carpeta)
    fotos = [p for p in imagenes(carpeta) if p.name in verdad][:limite]
    faltan = sorted(set(verdad) - {p.name for p in imagenes(carpeta)})
    if faltan:
        print(f"Aviso: en verdad.csv hay {len(faltan)} archivo(s) que no están en la carpeta: {', '.join(faltan[:5])}")
    print(f"Evaluando {len(fotos)} imágenes · IA: {proveedores_configurados(settings.VISION_PROVIDERS) or 'ninguna'} · "
          f"privacidad: {settings.VISION_PRIVACY_MODE}")
    resultados = []
    for i, p in enumerate(fotos, 1):
        r = evaluar_imagen(p, verdad[p.name], consentimiento, extraer)
        aciertos = sum(1 for v in r.campos.values() if v["acierto"])
        print(f"[{i}/{len(fotos)}] {p.name}: {r.metodo or 'error'} · {r.estado or '-'} · "
              f"{aciertos}/{sum(1 for v in r.campos.values() if v['acierto'] is not None)} campos · {r.segundos:.1f}s"
              + (f" · {r.error}" if r.error else ""))
        resultados.append(r)
        if pausa and i < len(fotos):
            time.sleep(pausa)   # respeta los límites por minuto del nivel gratuito
    nota = (f"Configuración: proveedores de visión `{settings.VISION_PROVIDERS}`, modo de privacidad "
            f"`{settings.VISION_PRIVACY_MODE}`, modelo Gemini `{settings.GEMINI_MODEL}`.")
    return guardar(resultados, carpeta / "resultados", settings.CONFIDENCE_THRESHOLD,
                   "Evaluación del módulo de visión con fotos reales", nota)


def cmd_bd(salida: Path) -> Path:
    """Mide con las facturas confirmadas en la app: lo que leyó la IA vs. lo que confirmó el usuario."""
    from app.core.config import settings
    from app.core.db import SessionLocal
    from app.models import Factura
    db = SessionLocal()
    try:
        facturas = db.query(Factura).filter(Factura.estado == "confirmada", Factura.datos_ia.isnot(None),
                                            Factura.metodo != "manual").all()
        resultados = []
        for f in facturas:
            ia = f.datos_ia or {}
            confirmado = {"proveedor": f.proveedor, "nit": f.nit, "fecha": f.fecha.isoformat() if f.fecha else None,
                          "total": f.total}
            r = Resultado(archivo=str(f.id)[:8], metodo=f.metodo or "", confianza=f.confianza or 0.0,
                          estado="por_confirmar")   # todas pasaron por revisión humana
            for c in CAMPOS:
                r.campos[c] = {"esperado": confirmado[c] if confirmado[c] is not None else "",
                               "obtenido": ia.get(c), "acierto": comparar(c, confirmado[c], ia.get(c)),
                               "confianza": (f.confianza_campos or {}).get(c)}
            resultados.append(r)
    finally:
        db.close()
    return guardar(resultados, salida, settings.CONFIDENCE_THRESHOLD, "Aciertos de la IA en facturas confirmadas por usuarios",
                   "Fuente: facturas con estado `confirmada`; lo esperado es lo que confirmó el usuario.")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Evaluación del módulo de visión (facturas).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plantilla", help="crea verdad.csv para llenar a mano")
    p.add_argument("carpeta", type=Path)
    e = sub.add_parser("evaluar", help="procesa las imágenes y genera el reporte")
    e.add_argument("carpeta", type=Path)
    e.add_argument("--consentimiento", action="store_true",
                   help="hay autorización de tratamiento de datos (necesario en modo 'externo')")
    e.add_argument("--pausa", type=float, default=4.0, help="segundos entre imágenes (nivel gratuito: ~15/min)")
    e.add_argument("--limite", type=int, default=None, help="evaluar solo las primeras N")
    b = sub.add_parser("bd", help="mide con las facturas ya confirmadas en la base de datos")
    b.add_argument("--salida", type=Path, default=RAIZ / "evaluacion" / "resultados_bd")
    a = ap.parse_args(argv)
    os.chdir(RAIZ)   # para que se lea el .env del proyecto
    if a.cmd == "plantilla":
        cmd_plantilla(a.carpeta)
    elif a.cmd == "evaluar":
        print("Reporte:", cmd_evaluar(a.carpeta.resolve(), a.consentimiento, a.pausa, a.limite))
    else:
        print("Reporte:", cmd_bd(a.salida))


if __name__ == "__main__":
    main()

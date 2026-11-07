"""Pruebas del script de evaluación del módulo de visión (scripts/evaluar_vision.py)."""
import csv
import io
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluar_vision as ev  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.models import Factura  # noqa: E402


@pytest.mark.parametrize("campo,esperado,obtenido,acierto", [
    ("proveedor", "FERRETERÍA EL TORNILLO S.A.S.", "Ferreteria El Tornillo SAS", True),
    ("proveedor", "Almacenes Éxito S.A.", "Almacenes Exito", True),
    ("proveedor", "Droguería La 80", "Papelería Central", False),
    ("nit", "800.197.268-4", "800197268-4", True),
    ("nit", "800197268", "800197268-4", True),          # la verdad sin DV compara solo la base
    ("nit", "800197268-4", "800197268-5", False),
    ("fecha", "20/10/2026", "2026-10-20", True),
    ("fecha", "2026-10-20", "2026-10-21", False),
    ("total", "35.700", 35700.0, True),
    ("total", "35700", 35800.0, False),
    ("total", "35700", None, False),
    ("nit", "", "800197268-4", None),                     # sin respuesta correcta no se evalúa
])
def test_comparar(campo, esperado, obtenido, acierto):
    assert ev.comparar(campo, esperado, obtenido) is acierto


def _foto(ruta: Path, color):
    Image.new("RGB", (200, 150), color).save(ruta, "JPEG")


def test_plantilla_y_evaluacion_completa(tmp_path, capsys):
    for nombre, color in (("a.jpg", "red"), ("b.jpg", "green"), ("c.jpg", "blue")):
        _foto(tmp_path / nombre, color)
    (tmp_path / "ilegible.png").write_bytes(b"no es una imagen")
    ev.cmd_plantilla(tmp_path)
    filas = list(csv.DictReader(open(tmp_path / "verdad.csv", encoding="utf-8-sig")))
    assert [f["archivo"] for f in filas] == ["a.jpg", "b.jpg", "c.jpg", "ilegible.png"]

    # verdad.csv guardado por Excel en español (separador ';')
    (tmp_path / "verdad.csv").write_text(
        "archivo;proveedor;nit;fecha;total\n"
        "a.jpg;Tornillo SAS;800197268-4;2026-10-20;35700\n"
        "b.jpg;Papelería Central;811000001-1;2026-10-21;100000\n"
        "c.jpg;Droguería La 80;900123456;2026-10-22;48000\n"
        "ilegible.png;X;1;2026-01-01;1\n", encoding="utf-8")

    alta = {c: 0.95 for c in ("proveedor", "nit", "fecha", "total", "items")}
    lecturas = {
        # a: todo correcto y con alta confianza → validada sin errores
        (255, 0, 0): {"proveedor": "Tornillo S.A.S.", "nit": "800197268-4", "fecha": "2026-10-20", "total": 35700, "confianza_campos": alta},
        # b: total mal leído pero con alta confianza → validada CON error (riesgo que el reporte debe mostrar)
        (0, 128, 0): {"proveedor": "Papeleria Central", "nit": "811000001-1", "fecha": "2026-10-21", "total": 10000, "confianza_campos": alta},
        # c: NIT con baja confianza → a revisión
        (0, 0, 255): {"proveedor": "Drogueria La 80", "nit": "900123457", "fecha": "2026-10-22", "total": 48000,
                      "confianza_campos": {**alta, "nit": 0.3}},
    }

    def extraer_falso(entrada, consentimiento):
        px = Image.open(io.BytesIO(entrada.imagen)).convert("RGB").getpixel((5, 5))
        clave = min(lecturas, key=lambda k: sum(abs(a - b) for a, b in zip(k, px)))
        return {"crudo": lecturas[clave], "metodo": "vision:prueba", "datos_enmascarados": [], "advertencias": [], "fallos": []}

    from app.services import validacion_factura
    validacion_factura.digito_verificacion  # (el NIT 811000001-1 es válido: DV 1)
    reporte = ev.cmd_evaluar(tmp_path, consentimiento=True, pausa=0, limite=None, extraer=extraer_falso)
    texto = reporte.read_text(encoding="utf-8")

    assert "| total | 4 | 2 | 50.0 % |" in texto          # a y c bien; b mal; ilegible sin lectura
    assert "| nit | 4 | 2 | 50.0 % |" in texto            # c y la ilegible fallan
    assert "**Validadas con algún error** (el riesgo real): 1 → b.jpg" in texto
    assert "ilegible.png" in texto and "la guía exige al menos 20" in texto
    resultados = list(csv.DictReader(open(tmp_path / "resultados" / "resultados.csv", encoding="utf-8-sig")))
    assert len(resultados) == 4 * 4
    fila_b = next(r for r in resultados if r["archivo"] == "b.jpg" and r["campo"] == "total")
    assert fila_b["acierto"] == "no" and fila_b["estado"] == "validada"


def test_medicion_desde_facturas_confirmadas(tmp_path):
    db = SessionLocal()
    from datetime import date
    db.add(Factura(proveedor="Tornillo SAS", nit="800197268-4", fecha=date(2026, 10, 20), total=35700,
                   estado="confirmada", metodo="vision:gemini", confianza=0.8,
                   confianza_campos={"proveedor": 0.9, "nit": 0.4, "fecha": 0.9, "total": 0.9},
                   datos_ia={"proveedor": "Tornillo S.A.S", "nit": "800197268-5", "fecha": "2026-10-20", "total": 35700}))
    db.commit(); db.close()
    texto = ev.cmd_bd(tmp_path).read_text(encoding="utf-8")
    assert "Aciertos de la IA en facturas confirmadas" in texto
    filas = list(csv.DictReader(open(tmp_path / "resultados.csv", encoding="utf-8-sig")))
    nit = [f for f in filas if f["campo"] == "nit" and f["esperado"] == "800197268-4" and f["obtenido"] == "800197268-5"]
    assert nit and nit[0]["acierto"] == "no"

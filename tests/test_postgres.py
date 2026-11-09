"""Pruebas que solo tienen sentido sobre PostgreSQL + pgvector (como en Supabase).

Se ejecutan con:  TEST_DATABASE_URL=postgresql+psycopg://... pytest
Con SQLite se omiten.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import text

from app.core.db import ES_POSTGRES, SessionLocal, engine
from app.models import Chunk, Documento, Factura, Proveedor, Repositorio
from app.services import automatizacion_facturas, rag_service

pytestmark = pytest.mark.skipif(not ES_POSTGRES, reason="requiere PostgreSQL (TEST_DATABASE_URL)")


def _vector(eje: int, dim: int = 768) -> list[float]:
    v = [0.0] * dim
    v[eje] = 1.0
    return v


def test_migraciones_aplicadas_y_registradas():
    from app.core import migraciones
    with engine.connect() as cx:
        aplicadas = [r[0] for r in cx.execute(text("SELECT version FROM schema_migrations ORDER BY version"))]
        tipo = cx.execute(text("SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                               "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'")).scalar()
    assert aplicadas == [p.name for p in sorted(migraciones.CARPETA.glob("[0-9][0-9][0-9]_*.sql"))]
    assert tipo == "vector(768)"
    assert migraciones.aplicar(engine) == []      # idempotente: una segunda vez no hace nada


def test_indice_vectorial_es_hnsw_y_lo_usa_la_busqueda():
    """Regresión: con el ivfflat de 001 la búsqueda devolvía vacío (índice sin entrenar)."""
    with engine.connect() as cx:
        indices = [r[0] for r in cx.execute(text("SELECT indexdef FROM pg_indexes WHERE tablename = 'chunks'"))]
    assert any("USING hnsw" in i for i in indices)
    assert not any("ivfflat" in i for i in indices)


def test_busqueda_vectorial_real_con_pgvector(monkeypatch):
    db = SessionLocal()
    repo = Repositorio(nombre=f"PG-{uuid.uuid4().hex[:6]}")
    db.add(repo); db.flush()
    docs = {}
    for nombre, eje in (("contrato.pdf", 0), ("factura.pdf", 1), ("informe.pdf", 2)):
        d = Documento(storage_key=f"k/{nombre}", nombre=nombre, repo_id=repo.id, estado="procesado")
        db.add(d); db.flush()
        db.add(Chunk(documento_id=d.id, contenido=f"texto de {nombre}", embedding=_vector(eje)))
        docs[nombre] = d
    db.commit()

    # la consulta "se parece" al eje 1 → el más cercano por coseno debe ser factura.pdf
    monkeypatch.setattr(rag_service, "_embed_query", lambda q: [0.1, 0.9] + [0.0] * 766)
    hits = rag_service._buscar(db, "¿cuánto vale la factura?", repo.nombre)
    assert [h["archivo"] for h in hits][:3] == ["factura.pdf", "contrato.pdf", "informe.pdf"]
    assert rag_service._buscar(db, "x", "repositorio-que-no-existe") == []
    db.close()


def test_totales_numeric_y_acumulados_del_proveedor():
    """En Postgres `total` es NUMERIC: el flujo debe sumar sin mezclar Decimal y float."""
    db = SessionLocal()
    nit = "860034313-7"
    f1 = Factura(proveedor="Banco X", nit=nit, fecha=date(2026, 10, 1), total=1000.5, estado="validada")
    f2 = Factura(proveedor="Banco X", nit=nit, fecha=date(2026, 10, 2), total=2000.25, estado="confirmada")
    db.add_all([f1, f2]); db.commit()
    for f in (f1, f2):
        automatizacion_facturas.procesar_factura(db, str(f.id), "test")
    db.expire_all()
    p = db.query(Proveedor).filter(Proveedor.nit == nit).one()
    assert p.facturas == 2 and abs(p.total_compras - 3000.75) < 0.001
    assert isinstance(db.query(Factura).filter(Factura.id == f1.id).one().total, float)
    db.close()

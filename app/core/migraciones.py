"""Aplica en orden los archivos `migrations/NNN_*.sql` que aún no se han ejecutado.

Se llama al arrancar la app (y desde `python -m app.core.migraciones`). Lleva el control en la
tabla `schema_migrations` y usa un candado de Postgres para que dos instancias que arrancan a la
vez no apliquen la misma migración dos veces. En SQLite (desarrollo y pruebas) no hace nada:
allí las tablas las crea SQLAlchemy.
"""
import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger("motor.migraciones")
CARPETA = Path(__file__).resolve().parents[2] / "migrations"
CANDADO = 815_001   # identificador arbitrario para pg_advisory_lock


def pendientes(aplicadas: set[str]) -> list[Path]:
    return [p for p in sorted(CARPETA.glob("[0-9][0-9][0-9]_*.sql")) if p.name not in aplicadas]


def aplicar(engine: Engine) -> list[str]:
    if engine.dialect.name != "postgresql":
        return []
    nuevas: list[str] = []
    # `candado` solo sostiene el pg_advisory_lock (es de sesión); cada migración corre en su
    # propia transacción con engine.begin(): todo o nada.
    with engine.connect() as candado:
        candado.execute(text("SELECT pg_advisory_lock(:k)"), {"k": CANDADO})
        candado.commit()
        try:
            with engine.begin() as tx:
                tx.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations ("
                                "version VARCHAR(200) PRIMARY KEY, aplicada_en TIMESTAMPTZ NOT NULL DEFAULT now())"))
                aplicadas = {r[0] for r in tx.execute(text("SELECT version FROM schema_migrations"))}
            for archivo in pendientes(aplicadas):
                with engine.begin() as tx:
                    tx.exec_driver_sql(archivo.read_text(encoding="utf-8"))
                    tx.execute(text("INSERT INTO schema_migrations (version) VALUES (:v)"), {"v": archivo.name})
                log.info("Migración aplicada: %s", archivo.name)
                nuevas.append(archivo.name)
        finally:
            candado.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": CANDADO})
            candado.commit()
    return nuevas


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from app.core.db import engine
    print("Aplicadas:", aplicar(engine) or "ninguna (la base ya estaba al día)")

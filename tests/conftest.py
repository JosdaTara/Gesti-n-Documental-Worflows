import os

# Debe ejecutarse antes de importar la app: BD aislada, sin IA real, sin scheduler y sin
# esperas entre reintentos.
#   - Por defecto: SQLite temporal (rápido, sin dependencias).
#   - Con TEST_DATABASE_URL=postgresql+psycopg://...: PostgreSQL + pgvector real. La BD se
#     BORRA y se recrea con las migraciones, así que use una base solo para pruebas.
_PG = os.environ.get("TEST_DATABASE_URL", "")
_DB = os.path.join(os.path.dirname(__file__), "test.db")
if _PG:
    os.environ["DATABASE_URL"] = _PG
else:
    if os.path.exists(_DB):
        os.remove(_DB)
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["GEMINI_API_KEY"] = ""
os.environ["GROQ_API_KEY"] = ""
os.environ["OLLAMA_BASE_URL"] = ""
os.environ["SUPABASE_URL"] = ""
os.environ["APP_ENV"] = "dev"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["RETRY_WAIT_SECONDS"] = "0"
os.environ["RETRY_ATTEMPTS"] = "3"
os.environ["CRON_SECRET"] = "cron-test"

from sqlalchemy import text  # noqa: E402
from app.core.db import Base, engine  # noqa: E402
import app.models  # noqa: E402,F401

if _PG:
    with engine.begin() as cx:
        cx.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        cx.execute(text("CREATE SCHEMA public"))
    from app.core import migraciones  # noqa: E402
    migraciones.aplicar(engine)

# Tablas creadas para cualquier archivo de pruebas, aunque no importe la app.
Base.metadata.create_all(bind=engine)

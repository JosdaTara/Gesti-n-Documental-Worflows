import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from app.core.config import settings
from app.core.db import Base, engine, SessionLocal
from app.api.routers import admin, alerts, auth, documents, search, vision
from app.services.llm import LLMNoDisponible, proveedores_configurados

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.services.auth_service import bootstrap_admin, validar_configuracion_produccion
    from app.workers import scheduler
    validar_configuracion_produccion()
    from app.core import migraciones
    try:
        migraciones.aplicar(engine)      # Postgres: aplica migrations/*.sql pendientes
    except Exception as e:
        host = engine.url.host or "?"
        logging.getLogger("motor").error(
            "No se pudo conectar a la base de datos (host %s, puerto %s): %s. Revise DATABASE_URL: en Supabase "
            "use Connect → 'Transaction pooler' (puerto 6543, host *.pooler.supabase.com), con el prefijo "
            "postgresql+psycopg:// y la contraseña correcta. La conexión directa db.*.supabase.co es solo IPv6 "
            "y no funciona desde Render.", host, engine.url.port, type(e).__name__)
        raise
    Base.metadata.create_all(bind=engine)  # SQLite: crea las tablas; en Postgres no toca las existentes
    from app.services import storage
    storage.asegurar_bucket()
    db = SessionLocal()
    try:
        bootstrap_admin(db)
    finally:
        db.close()
    if settings.SCHEDULER_ENABLED:
        scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Gestión Documental con IA", version="0.2.0", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(CORSMiddleware, allow_origins=settings.ALLOWED_ORIGINS.split(","),
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(search.router)
app.include_router(vision.router)
app.include_router(admin.router)
app.include_router(alerts.router)

try:
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
except Exception:
    pass


@app.exception_handler(LLMNoDisponible)
async def _ia_no_disponible(_: Request, e: LLMNoDisponible):
    # Plan B final: respuesta clara al usuario cuando ningún modelo gratuito responde.
    return JSONResponse(status_code=503, content={"detail": str(e)})


@app.get("/", include_in_schema=False)
def inicio():
    return RedirectResponse("/static/index.html")


@app.get("/health")
def health():
    """Render la consulta para saber si el servicio está vivo. Verifica también la base de datos."""
    from sqlalchemy import text
    try:
        with engine.connect() as cx:
            cx.execute(text("SELECT 1"))
        bd = "ok"
    except Exception as e:
        return JSONResponse(status_code=503, content={"ok": False, "bd": f"error: {type(e).__name__}"})
    return {"ok": True, "bd": bd, "ia": proveedores_configurados(),
            "almacenamiento": "supabase" if settings.SUPABASE_URL else "local"}

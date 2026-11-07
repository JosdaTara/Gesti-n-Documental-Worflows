import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.core.config import settings
from app.core.db import Base, engine, SessionLocal
from app.api.routers import admin, auth, documents, search, vision
from app.services.llm import LLMNoDisponible, proveedores_configurados

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.services.auth_service import bootstrap_admin
    from app.workers import scheduler
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

app.add_middleware(CORSMiddleware, allow_origins=settings.ALLOWED_ORIGINS.split(","),
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

Base.metadata.create_all(bind=engine)

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(search.router)
app.include_router(vision.router)
app.include_router(admin.router)

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
    return {"ok": True, "ia": proveedores_configurados()}

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.core.config import settings
from app.core.db import Base, engine
from app.api.routers import auth, documents, search, vision

app = FastAPI(title="Gestión Documental con IA", version="0.1.0")

app.add_middleware(CORSMiddleware, allow_origins=settings.ALLOWED_ORIGINS.split(","),
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

Base.metadata.create_all(bind=engine)

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(search.router)
app.include_router(vision.router)

try:
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
except Exception:
    pass

@app.on_event("startup")
def _startup():
    from app.services.auth_service import bootstrap_admin
    from app.core.db import SessionLocal
    db = SessionLocal()
    try:
        bootstrap_admin(db)
        from app.workers.scheduler import start
        start()
    finally:
        db.close()

@app.get("/health")
def health():
    return {"ok": True}

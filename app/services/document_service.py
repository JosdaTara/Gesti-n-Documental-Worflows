import os, hashlib, uuid
from sqlalchemy.orm import Session
from fastapi import HTTPException, UploadFile
from app.models import Documento, Repositorio
from app.services import storage
from app.core.logging import log_execution, con_reintentos

ALLOWED = {"pdf", "docx", "txt"}
MAX_BYTES = 20 * 1024 * 1024

def listar(db: Session):
    docs = db.query(Documento).order_by(Documento.fecha.desc()).all()
    repos = [r.nombre for r in db.query(Repositorio).all()]
    return {"documentos": [
        {"id": str(d.id), "nombre": d.nombre, "repo": d.repo_id and _repo_name(db, d.repo_id) or "General",
         "tipo": d.tipo or "", "estado": d.estado or "procesando", "categoria": d.categoria or "",
         "fecha": d.fecha.strftime("%Y-%m-%d"), "resumen": d.resumen or "", "datos": d.datos, "detalle": d.detalle or ""}
        for d in docs], "repos": repos or ["General"]}

def _repo_name(db, repo_id):
    r = db.query(Repositorio).filter(Repositorio.id == repo_id).first()
    return r.nombre if r else "General"

def crear_repo(db: Session, nombre: str, usuario_id: int | None = None):
    with log_execution(db, "crear_repo", "web", {"nombre": nombre, "usuario_id": usuario_id}):
        if db.query(Repositorio).filter(Repositorio.nombre == nombre).first():
            raise HTTPException(status_code=400, detail="Ese repositorio ya existe")
        db.add(Repositorio(nombre=nombre)); db.commit()
        return {"ok": True}

async def subir(db: Session, repo: str, file: UploadFile, usuario_id: int):
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED:
        raise HTTPException(status_code=400, detail=f"{file.filename}: formato no permitido")
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=400, detail=f"{file.filename}: supera los 20 MB")
    with log_execution(db, "subir_documento", "web",
                       {"nombre": file.filename, "repo": repo, "usuario_id": usuario_id}) as rec:
        key, h = con_reintentos(storage.save_bytes)(data, file.content_type or "application/octet-stream", "docs")
        r = db.query(Repositorio).filter(Repositorio.nombre == repo).first()
        if not r:
            r = Repositorio(nombre=repo); db.add(r); db.commit(); db.refresh(r)
        d = Documento(storage_key=key, nombre=file.filename, repo_id=r.id, tipo=ext.upper(),
                      estado="pendiente", usuario_id=usuario_id, hash_sha256=h, tamano=len(data))
        db.add(d); db.commit(); db.refresh(d)
        rec.resultado = {"id": str(d.id)}
        return {"ok": True, "id": str(d.id)}

def _get(db: Session, doc_id: str) -> Documento:
    try:
        d = db.query(Documento).filter(Documento.id == uuid.UUID(str(doc_id))).first()
    except ValueError:
        d = None
    if not d:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    return d

def eliminar(db: Session, doc_id: str, usuario_rol: str, usuario_id: int | None = None):
    with log_execution(db, "eliminar_documento", "web", {"id": doc_id, "usuario_id": usuario_id}):
        if usuario_rol != "admin":
            raise HTTPException(status_code=403, detail="Solo un administrador puede eliminar documentos.")
        d = _get(db, doc_id)
        db.delete(d); db.commit()
        return {"ok": True}

def reintentar(db: Session, doc_id: str, usuario_id: int | None = None):
    """Devuelve el documento a `pendiente` y lo indexa de inmediato (antes: acción `reintentar`)."""
    with log_execution(db, "reintentar_documento", "web", {"id": doc_id, "usuario_id": usuario_id}):
        d = _get(db, doc_id)
        d.estado = "pendiente"; d.detalle = None; db.commit()
    from app.services.indexing_service import index_one
    try:
        index_one(db, doc_id, "reintento_manual")
    except Exception:
        pass  # el error queda en el documento y en `ejecuciones`
    d = _get(db, doc_id)
    return {"ok": True, "estado": d.estado, "detalle": d.detalle or ""}

def descargar(db: Session, doc_id: str, usuario_id: int | None = None):
    with log_execution(db, "descargar_documento", "web", {"id": doc_id, "usuario_id": usuario_id}):
        d = _get(db, doc_id)
        return {"url": con_reintentos(storage.signed_url)(d.storage_key)}

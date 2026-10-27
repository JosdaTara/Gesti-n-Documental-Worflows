import os, hashlib, uuid
from sqlalchemy.orm import Session
from fastapi import HTTPException, UploadFile
from app.models import Documento, Repositorio
from app.services import storage
from app.core.logging import log_execution

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

def crear_repo(db: Session, nombre: str):
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
    with log_execution(db, "subir", "web", {"nombre": file.filename, "repo": repo}):
        key, h = storage.save_bytes(data, file.content_type or "application/octet-stream", "docs")
        r = db.query(Repositorio).filter(Repositorio.nombre == repo).first()
        if not r:
            r = Repositorio(nombre=repo); db.add(r); db.commit(); db.refresh(r)
        d = Documento(storage_key=key, nombre=file.filename, repo_id=r.id, tipo=ext.upper(),
                      estado="pendiente", usuario_id=usuario_id, hash_sha256=h, tamano=len(data))
        db.add(d); db.commit(); db.refresh(d)
        return {"ok": True, "id": str(d.id)}

def eliminar(db: Session, doc_id: str, usuario_rol: str):
    if usuario_rol != "admin":
        raise HTTPException(status_code=403, detail="Solo un administrador puede eliminar documentos.")
    d = db.query(Documento).filter(Documento.id == doc_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    with log_execution(db, "eliminar", "web", {"id": doc_id}):
        db.delete(d); db.commit()
    return {"ok": True}

def reintentar(db: Session, doc_id: str):
    d = db.query(Documento).filter(Documento.id == doc_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    d.estado = "pendiente"; d.detalle = None; db.commit()
    return {"ok": True}

def descargar(db: Session, doc_id: str):
    d = db.query(Documento).filter(Documento.id == doc_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    return {"url": storage.signed_url(d.storage_key)}

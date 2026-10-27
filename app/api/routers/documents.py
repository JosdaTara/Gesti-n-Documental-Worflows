from fastapi import APIRouter, Depends, UploadFile, File, Form
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.core.security import get_current_user
from app.schemas import ListarOut, RepoCreate
from app.services import document_service as ds

router = APIRouter(prefix="/api", tags=["documents"])

@router.get("/documents", response_model=ListarOut)
def listar(db: Session = Depends(get_db), u=Depends(get_current_user)):
    return ds.listar(db)

@router.post("/repositories")
def crear_repo(body: RepoCreate, db: Session = Depends(get_db), u=Depends(get_current_user)):
    return ds.crear_repo(db, body.nombre)

@router.post("/documents")
async def subir(repo: str = Form(...), file: UploadFile = File(...),
                db: Session = Depends(get_db), u=Depends(get_current_user)):
    return await ds.subir(db, repo, file, u["id"])

@router.delete("/documents/{doc_id}")
def eliminar(doc_id: str, db: Session = Depends(get_db), u=Depends(get_current_user)):
    return ds.eliminar(db, doc_id, u["rol"])

@router.post("/documents/{doc_id}/retry")
def reintentar(doc_id: str, db: Session = Depends(get_db), u=Depends(get_current_user)):
    return ds.reintentar(db, doc_id)

@router.get("/documents/{doc_id}/download")
def descargar(doc_id: str, db: Session = Depends(get_db), u=Depends(get_current_user)):
    return ds.descargar(db, doc_id)

@router.post("/admin/run-indexer")
def run_indexer(db: Session = Depends(get_db)):
    from app.services.indexing_service import sweep_pending
    sweep_pending(db)
    return {"ok": True}

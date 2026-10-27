from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.core.security import get_current_user
from app.schemas import BuscarIn, BuscarOut, ConsultarIn, ConsultarOut
from app.services import rag_service

router = APIRouter(prefix="/api", tags=["search"])

@router.post("/search", response_model=BuscarOut)
def buscar(body: BuscarIn, db: Session = Depends(get_db), u=Depends(get_current_user)):
    return {"resultados": rag_service.search(db, body.q, body.repo or "")}

@router.post("/chat", response_model=ConsultarOut)
def chat(body: ConsultarIn, db: Session = Depends(get_db), u=Depends(get_current_user)):
    return rag_service.answer(db, body.pregunta, body.repo or "")

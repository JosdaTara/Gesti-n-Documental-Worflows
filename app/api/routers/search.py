from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.core.security import get_current_user
from app.schemas import BuscarIn, BuscarOut, ConsultarIn, ConsultarOut
from app.services import chatbot, rag_service

router = APIRouter(prefix="/api", tags=["search"])


@router.post("/search", response_model=BuscarOut)
def buscar(body: BuscarIn, db: Session = Depends(get_db), u=Depends(get_current_user)):
    return {"resultados": rag_service.search(db, body.q, body.repo or "", u["id"])}


@router.post("/rag/consultar", response_model=ConsultarOut)
def consultar_rag(body: ConsultarIn, db: Session = Depends(get_db), u=Depends(get_current_user)):
    """WF3 tal como era en n8n: recupera fragmentos y responde con el LLM (sin memoria)."""
    return rag_service.answer(db, body.pregunta, body.repo or "", u["id"])


@router.post("/chat", response_model=ConsultarOut)
def chat(body: ConsultarIn, db: Session = Depends(get_db), u=Depends(get_current_user)):
    """Chatbot con herramientas y memoria (LangGraph)."""
    return chatbot.responder(db, u["id"], body.pregunta, body.sesion, body.repo or "")


@router.get("/chat/historial")
def ver_historial(sesion: str = Query(..., max_length=80), db: Session = Depends(get_db),
                  u=Depends(get_current_user)):
    return {"mensajes": chatbot.historial(db, u["id"], sesion)}


@router.delete("/chat/historial")
def borrar_historial(sesion: str = Query(..., max_length=80), db: Session = Depends(get_db),
                     u=Depends(get_current_user)):
    return {"ok": True, "borrados": chatbot.borrar_historial(db, u["id"], sesion)}

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.core.security import require_permiso
from app.schemas import BuscarIn, BuscarOut, ConsultarIn, ConsultarOut
from app.services import chatbot, rag_service

router = APIRouter(prefix="/api", tags=["search"])


@router.post("/search", response_model=BuscarOut)
def buscar(body: BuscarIn, db: Session = Depends(get_db), u=Depends(require_permiso("usar_chatbot"))):
    return {"resultados": rag_service.search(db, body.q, body.repo or "", u["id"])}


@router.post("/rag/consultar", response_model=ConsultarOut)
def consultar_rag(body: ConsultarIn, db: Session = Depends(get_db), u=Depends(require_permiso("usar_chatbot"))):
    """WF3 tal como era en n8n: recupera fragmentos y responde con el LLM (sin memoria)."""
    return rag_service.answer(db, body.pregunta, body.repo or "", u["id"])


@router.post("/chat", response_model=ConsultarOut)
def chat(body: ConsultarIn, db: Session = Depends(get_db), u=Depends(require_permiso("usar_chatbot"))):
    """Chatbot con herramientas y memoria (LangGraph)."""
    return chatbot.responder(db, u["id"], body.pregunta, body.sesion, body.repo or "", usuario=u)


@router.post("/chat/imagen", response_model=ConsultarOut)
async def chat_imagen(file: UploadFile = File(...), sesion: str = Form("general"), consentimiento: bool = Form(False),
                      db: Session = Depends(get_db), u=Depends(require_permiso("subir_imagenes"))):
    """Canal chatbot del módulo de visión: recibe la foto de una factura por el chat."""
    return chatbot.recibir_imagen(db, u, await file.read(), file.content_type or "", sesion, consentimiento)


@router.get("/chat/historial")
def ver_historial(sesion: str = Query(..., max_length=80), db: Session = Depends(get_db),
                  u=Depends(require_permiso("usar_chatbot"))):
    return {"mensajes": chatbot.historial(db, u["id"], sesion)}


@router.delete("/chat/historial")
def borrar_historial(sesion: str = Query(..., max_length=80), db: Session = Depends(get_db),
                     u=Depends(require_permiso("usar_chatbot"))):
    return {"ok": True, "borrados": chatbot.borrar_historial(db, u["id"], sesion)}

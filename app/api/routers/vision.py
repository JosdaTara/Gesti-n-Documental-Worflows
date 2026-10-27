from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.core.security import get_current_user
from app.services import vision_service as vs
from app.schemas import VisionOut

router = APIRouter(prefix="/api/vision", tags=["vision"])

@router.post("/invoices", response_model=VisionOut)
async def procesar_factura(file: UploadFile = File(...), db: Session = Depends(get_db),
                          u=Depends(get_current_user)):
    data = await file.read()
    return vs.process_invoice(db, data, file.content_type or "image/jpeg", u["id"])

@router.post("/invoices/{img_id}/confirm")
def confirmar(img_id: str, db: Session = Depends(get_db), u=Depends(get_current_user)):
    return {"ok": True, "id": img_id}

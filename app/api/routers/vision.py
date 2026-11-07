from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.core.security import get_current_user
from app.schemas import FacturaEdit, FacturaOut
from app.services import vision_service as vs

router = APIRouter(prefix="/api/vision", tags=["vision"])


@router.post("/invoices", response_model=FacturaOut)
async def procesar_factura(file: UploadFile = File(...), consentimiento: bool = Form(False),
                           db: Session = Depends(get_db), u=Depends(get_current_user)):
    """Sube una foto o PDF de factura y devuelve los datos extraídos con su confianza."""
    data = await file.read()
    return vs.process_invoice(db, data, file.content_type or "", u["id"], consentimiento)


@router.get("/invoices", response_model=list[FacturaOut])
def listar(estado: str | None = Query(default=None, pattern="^(por_confirmar|validada|confirmada)$"),
           limite: int = Query(default=50, ge=1, le=200),
           db: Session = Depends(get_db), u=Depends(get_current_user)):
    return vs.listar(db, u, estado, limite)


@router.get("/invoices/{factura_id}", response_model=FacturaOut)
def ver(factura_id: str, db: Session = Depends(get_db), u=Depends(get_current_user)):
    return vs.factura_out(vs.obtener(db, factura_id, u))


@router.post("/invoices/{factura_id}/confirm", response_model=FacturaOut)
def confirmar(factura_id: str, body: FacturaEdit, db: Session = Depends(get_db), u=Depends(get_current_user)):
    """Revisión humana: guarda los datos corregidos y marca la factura como confirmada."""
    return vs.confirmar(db, factura_id, body.model_dump(exclude_unset=True), u)


@router.get("/images/{imagen_id}")
def ver_imagen(imagen_id: str, db: Session = Depends(get_db), u=Depends(get_current_user)):
    data, mime = vs.imagen(db, imagen_id, u)
    return Response(content=data, media_type=mime, headers={"Cache-Control": "private, no-store"})

import os, json, hashlib, io
from sqlalchemy.orm import Session
from app.models import Documento, Chunk, Repositorio
from app.services import storage
from app.core.config import settings
from app.core.logging import log_execution

def _extract_text(data: bytes, nombre: str) -> str:
    ext = nombre.rsplit(".", 1)[-1].lower()
    if ext == "pdf":
        from pypdf import PdfReader
        r = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in r.pages)
    if ext == "docx":
        import docx
        d = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in d.paragraphs)
    return data.decode("utf-8", errors="ignore")

def _chunk(text: str, size=1000, overlap=150):
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i+size])
        i += size - overlap
    return [c for c in out if c.strip()]

def _embed(texts):
    import google.generativeai as genai
    genai.configure(api_key=settings.GEMINI_API_KEY)
    res = genai.embed_content(model=f"models/{settings.EMBEDDING_MODEL}", content=texts,
                              task_type="retrieval_document", output_dimensionality=settings.EMBEDDING_DIM)
    return res["embedding"] if isinstance(res["embedding"][0], (int, float)) else res["embedding"]

def _classify(text: str) -> dict:
    import google.generativeai as genai
    genai.configure(api_key=settings.GEMINI_API_KEY)
    m = genai.GenerativeModel(settings.GEMINI_MODEL)
    prompt = ("Clasifica y resume este documento. Devuelve SOLO JSON valido con claves "
              "categoria (Contrato|Factura|Informe|Otro), resumen (2-3 frases) y datos (objeto con hasta 5 campos).\n\n"
              + text[:8000])
    r = m.generate_content(prompt, generation_config={"response_mime_type": "application/json", "temperature": 0.2})
    try:
        import json as _j
        o = _j.loads(r.text)
        return {"categoria": o.get("categoria", "Otro"), "resumen": o.get("resumen", ""),
                "datos": o.get("datos", {}), "detalle": ""}
    except Exception as e:
        return {"categoria": "", "resumen": "", "datos": {}, "detalle": f"Clasificacion IA fallida: {e}"}

def index_one(db: Session, doc_id: str):
    d = db.query(Documento).filter(Documento.id == doc_id).first()
    if not d:
        return
    with log_execution(db, "indexar", "scheduler", {"id": doc_id}):
        try:
            data = storage.read_bytes(d.storage_key)
            text = _extract_text(data, d.nombre)
            chunks = _chunk(text)
            if chunks:
                vecs = _embed(chunks)
                for c, v in zip(chunks, vecs):
                    db.add(Chunk(documento_id=d.id, contenido=c, embedding=v,
                                meta={"archivo": d.nombre}))
            cls = _classify(text)
            d.categoria = cls["categoria"]; d.resumen = cls["resumen"]
            d.datos = cls["datos"]; d.detalle = cls["detalle"]; d.estado = "procesado"
            db.commit()
        except Exception as e:
            d.estado = "error"; d.detalle = str(e); db.commit()
            raise

def sweep_pending(db: Session):
    pending = db.query(Documento).filter(Documento.estado.in_(["pendiente", "error"])).all()
    for d in pending:
        try:
            index_one(db, str(d.id))
        except Exception:
            continue

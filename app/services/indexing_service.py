import os, json, hashlib, io
from sqlalchemy.orm import Session
from app.models import Documento, Chunk, Repositorio
from app.services import storage
from app.core.config import settings
from app.core.logging import log_execution, con_reintentos
from app.services import llm

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
    return llm.embed(texts, "RETRIEVAL_DOCUMENT")


def _llm_json(prompt: str) -> dict:
    """Clasificación con plan B (Gemini → Groq → Ollama)."""
    datos, _proveedor = llm.invocar_json(prompt, accion="clasificar_documento")
    return datos


def _classify(text: str) -> dict:
    prompt = ("Clasifica y resume este documento. Devuelve SOLO JSON valido con claves "
              "categoria (Contrato|Factura|Informe|Otro), resumen (2-3 frases) y datos (objeto con hasta 5 campos).\n\n"
              + text[:8000])
    try:
        o = _llm_json(prompt)  # si ningún modelo responde, LLMNoDisponible sube y el doc queda en error
        return {"categoria": o.get("categoria", "Otro"), "resumen": o.get("resumen", ""),
                "datos": o.get("datos", {}), "detalle": ""}
    except ValueError as e:
        return {"categoria": "", "resumen": "", "datos": {}, "detalle": f"Respuesta de la IA no valida: {e}"}


def index_one(db: Session, doc_id: str, disparador: str = "scheduler") -> dict:
    """WF1: descarga, extrae texto, genera embeddings, clasifica y marca el documento."""
    d = db.query(Documento).filter(Documento.id == _uuid(doc_id)).first()
    if not d:
        return {"ok": False, "detalle": "no existe"}
    with log_execution(db, "indexar_documento", disparador, {"id": doc_id, "nombre": d.nombre}) as rec:
        try:
            data = con_reintentos(storage.read_bytes)(d.storage_key)
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
            rec.resultado = {"chunks": len(chunks), "categoria": d.categoria}
            return rec.resultado
        except Exception as e:
            db.rollback()
            d.estado = "error"; d.detalle = str(e)[:500]; db.commit()
            raise


def sweep_pending(db: Session, disparador: str = "cron") -> dict:
    """Barrido periódico (antes: Schedule Trigger de n8n cada 5 min).

    Solo toma documentos `pendiente`. Los que quedaron en `error` se reprocesan cuando
    un usuario pulsa Reintentar, para no gastar la cuota gratuita de la IA en bucle.
    """
    with log_execution(db, "barrido_indexacion", disparador) as rec:
        ids = [str(d.id) for d in db.query(Documento.id).filter(Documento.estado == "pendiente").all()]
        ok = err = 0
        for doc_id in ids:
            try:
                index_one(db, doc_id, disparador)
                ok += 1
            except Exception:
                err += 1
        rec.resultado = {"pendientes": len(ids), "procesados": ok, "errores": err}
        return rec.resultado


def _uuid(v):
    import uuid
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))

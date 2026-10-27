from sqlalchemy.orm import Session
from sqlalchemy import text
from app.models import Chunk, Documento, Repositorio
from app.core.config import settings

def _embed_query(q: str):
    import google.generativeai as genai
    genai.configure(api_key=settings.GEMINI_API_KEY)
    r = genai.embed_content(model=f"models/{settings.EMBEDDING_MODEL}", content=q,
                            task_type="retrieval_query", output_dimensionality=settings.EMBEDDING_DIM)
    v = r["embedding"]
    return v[0] if isinstance(v[0], list) else v

def search(db: Session, q: str, repo: str = ""):
    vec = _embed_query(q)
    sql = """SELECT c.contenido, d.nombre, d.id::text, r.nombre AS repo
             FROM chunks c JOIN documentos d ON d.id=c.documento_id
             LEFT JOIN repositorios r ON r.id=d.repo_id
             WHERE (:repo = '' OR r.nombre = :repo)
             ORDER BY c.embedding <=> CAST(:vec AS vector) LIMIT 6"""
    rows = db.execute(text(sql), {"vec": str(vec), "repo": repo or ""}).fetchall()
    return [{"archivo": r.nombre, "fragmento": (r.contenido or "")[:600], "pagina": None} for r in rows]

def answer(db: Session, pregunta: str, repo: str = ""):
    hits = search(db, pregunta, repo)
    if not hits:
        return {"respuesta": "No encontré fragmentos relevantes en los documentos.", "fuentes": []}
    ctx = "\n\n".join(f"[{i+1}] Archivo: {h['archivo']}\n{h['fragmento']}" for i, h in enumerate(hits))
    import google.generativeai as genai
    genai.configure(api_key=settings.GEMINI_API_KEY)
    m = genai.GenerativeModel(settings.GEMINI_MODEL)
    prompt = ("Eres el asistente de un repositorio documental. Responde en español usando SOLO los fragmentos. "
              "Si la respuesta no está, dilo con claridad.\n\nFragmentos:\n" + ctx + "\n\nPregunta: " + pregunta)
    r = m.generate_content(prompt, generation_config={"temperature": 0.3})
    fuentes = [{"archivo": h["archivo"]} for h in hits]
    # dedup
    seen = set(); uniq = []
    for f in fuentes:
        if f["archivo"] not in seen:
            seen.add(f["archivo"]); uniq.append(f)
    return {"respuesta": (r.text or "").strip(), "fuentes": uniq}

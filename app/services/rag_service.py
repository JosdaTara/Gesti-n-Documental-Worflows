"""WF3: agente RAG (antes webhook de n8n con acciones `buscar` y `consultar`)."""
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.config import settings
from app.core.logging import log_execution, con_reintentos


@con_reintentos
def _embed_query(q: str):
    import google.generativeai as genai
    genai.configure(api_key=settings.GEMINI_API_KEY)
    r = genai.embed_content(model=f"models/{settings.EMBEDDING_MODEL}", content=q,
                            task_type="retrieval_query", output_dimensionality=settings.EMBEDDING_DIM)
    v = r["embedding"]
    return v[0] if isinstance(v[0], list) else v


@con_reintentos
def _generar(prompt: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=settings.GEMINI_API_KEY)
    m = genai.GenerativeModel(settings.GEMINI_MODEL)
    r = m.generate_content(prompt, generation_config={"temperature": 0.3})
    return (r.text or "").strip()


def _buscar(db: Session, q: str, repo: str = ""):
    vec = _embed_query(q)
    sql = """SELECT c.contenido, d.nombre, d.id::text, r.nombre AS repo
             FROM chunks c JOIN documentos d ON d.id=c.documento_id
             LEFT JOIN repositorios r ON r.id=d.repo_id
             WHERE (:repo = '' OR r.nombre = :repo)
             ORDER BY c.embedding <=> CAST(:vec AS vector) LIMIT 6"""
    rows = db.execute(text(sql), {"vec": str(vec), "repo": repo or ""}).fetchall()
    return [{"archivo": r.nombre, "fragmento": (r.contenido or "")[:600], "pagina": None} for r in rows]


def search(db: Session, q: str, repo: str = "", usuario_id: int | None = None):
    with log_execution(db, "rag_buscar", "web", {"q": q, "repo": repo, "usuario_id": usuario_id}) as rec:
        hits = _buscar(db, q, repo)
        rec.resultado = {"resultados": len(hits)}
        return hits


def answer(db: Session, pregunta: str, repo: str = "", usuario_id: int | None = None):
    with log_execution(db, "rag_consultar", "web",
                       {"pregunta": pregunta, "repo": repo, "usuario_id": usuario_id}) as rec:
        hits = _buscar(db, pregunta, repo)
        if not hits:
            rec.resultado = {"fuentes": 0}
            return {"respuesta": "No encontré fragmentos relevantes en los documentos.", "fuentes": []}
        ctx = "\n\n".join(f"[{i+1}] Archivo: {h['archivo']}\n{h['fragmento']}" for i, h in enumerate(hits))
        prompt = ("Eres el asistente de un repositorio documental. Responde en español usando SOLO los fragmentos. "
                  "Si la respuesta no está, dilo con claridad.\n\nFragmentos:\n" + ctx + "\n\nPregunta: " + pregunta)
        respuesta = _generar(prompt)
        uniq = list(dict.fromkeys(h["archivo"] for h in hits))
        rec.resultado = {"fuentes": len(uniq)}
        return {"respuesta": respuesta, "fuentes": [{"archivo": a} for a in uniq]}

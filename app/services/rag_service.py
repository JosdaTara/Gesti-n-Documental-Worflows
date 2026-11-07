"""WF3: agente RAG (antes webhook de n8n con acciones `buscar` y `consultar`)."""
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.logging import log_execution
from app.services import llm


def _embed_query(q: str):
    return llm.embed([q], "RETRIEVAL_QUERY")[0]


def _generar(prompt: str, usuario_id: int | None = None) -> str:
    return llm.invocar(prompt, accion="rag_respuesta", usuario_id=usuario_id, temperatura=0.3).texto


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
        respuesta = _generar(prompt, usuario_id)
        uniq = list(dict.fromkeys(h["archivo"] for h in hits))
        rec.resultado = {"fuentes": len(uniq)}
        return {"respuesta": respuesta, "fuentes": [{"archivo": a} for a in uniq]}

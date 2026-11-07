"""Chatbot del aplicativo: agente LangGraph con herramientas y memoria por usuario.

Reemplaza al agente RAG de n8n. El agente decide qué herramienta usar:
- `buscar_documentos`: búsqueda semántica en el repositorio (RAG).
- `listar_documentos`: estado del repositorio (cuántos, cuáles con error, por categoría).
- `consultar_facturas`: facturas registradas (las crea el módulo de visión).

La memoria se guarda en la tabla `conversaciones`, así sobrevive a reinicios del servidor.
Plan B: si el proveedor principal falla, el mismo agente se ejecuta con el siguiente
(Gemini → Groq → Ollama). Si ninguno responde se lanza `LLMNoDisponible`.
"""
import time

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from sqlalchemy.orm import Session

from app.core.audit import auditar
from app.core.config import settings
from app.core.logging import log_execution
from app.models import Conversacion, Documento, Factura, Repositorio
from app.services import llm

SISTEMA = """Eres el asistente del sistema de gestión documental de la empresa.
Responde siempre en español, de forma breve y concreta.
- Para preguntas sobre el contenido de los documentos usa `buscar_documentos` y responde SOLO con lo que digan los fragmentos; cita el nombre del archivo.
- Para preguntas sobre cuántos documentos hay, su estado o errores usa `listar_documentos`.
- Para preguntas sobre facturas, proveedores, NIT o totales usa `consultar_facturas`.
- Si la información no está, dilo claramente. No inventes datos.
{filtro}"""


def _herramientas(db: Session, repo_defecto: str, fuentes: list[str]):
    @tool
    def buscar_documentos(consulta: str, repositorio: str = "") -> str:
        """Busca fragmentos relevantes en los documentos indexados. Úsala para preguntas sobre su contenido."""
        from app.services.rag_service import _buscar
        hits = _buscar(db, consulta, repositorio or repo_defecto)
        if not hits:
            return "No se encontraron fragmentos relevantes."
        for h in hits:
            if h["archivo"] not in fuentes:
                fuentes.append(h["archivo"])
        return "\n\n".join(f"[{i+1}] Archivo: {h['archivo']}\n{h['fragmento']}" for i, h in enumerate(hits))

    @tool
    def listar_documentos(estado: str = "", categoria: str = "") -> str:
        """Lista documentos del repositorio. estado: pendiente|procesado|error. categoria: Contrato|Factura|Informe|Otro."""
        q = db.query(Documento)
        if estado:
            q = q.filter(Documento.estado == estado)
        if categoria:
            q = q.filter(Documento.categoria == categoria)
        if repo_defecto:
            q = q.join(Repositorio, Repositorio.id == Documento.repo_id).filter(Repositorio.nombre == repo_defecto)
        total = q.count()
        docs = q.order_by(Documento.fecha.desc()).limit(20).all()
        filas = [f"- {d.nombre} | estado: {d.estado} | categoría: {d.categoria or 'sin clasificar'}"
                 + (f" | detalle: {d.detalle[:120]}" if d.detalle else "") for d in docs]
        return f"Total: {total} documento(s).\n" + "\n".join(filas) if filas else "No hay documentos con ese filtro."

    @tool
    def consultar_facturas(proveedor: str = "", nit: str = "", estado: str = "") -> str:
        """Consulta facturas registradas. Filtros opcionales: proveedor (texto parcial), nit, estado (por_confirmar|validada)."""
        q = db.query(Factura)
        if proveedor:
            q = q.filter(Factura.proveedor.ilike(f"%{proveedor}%"))
        if nit:
            q = q.filter(Factura.nit == nit)
        if estado:
            q = q.filter(Factura.estado == estado)
        facturas = q.order_by(Factura.creado_en.desc()).limit(10).all()
        if not facturas:
            return "No hay facturas registradas con ese filtro."
        return "\n".join(f"- {f.proveedor or '¿?'} | NIT {f.nit or '¿?'} | fecha {f.fecha or '¿?'} | "
                         f"total {f.total if f.total is not None else '¿?'} {f.moneda or ''} | estado {f.estado}"
                         for f in facturas)

    return [buscar_documentos, listar_documentos, consultar_facturas]


def _historial(db: Session, usuario_id: int, sesion: str):
    filas = (db.query(Conversacion)
             .filter(Conversacion.usuario_id == usuario_id, Conversacion.sesion == sesion)
             .order_by(Conversacion.id.desc()).limit(settings.CHAT_HISTORY_TURNS).all())
    return [HumanMessage(f.contenido) if f.rol == "user" else AIMessage(f.contenido) for f in reversed(filas)]


def responder(db: Session, usuario_id: int, pregunta: str, sesion: str | None = None, repo: str = "") -> dict:
    sesion = (sesion or "general")[:80]
    with log_execution(db, "chatbot", "web", {"pregunta": pregunta, "sesion": sesion, "repo": repo,
                                              "usuario_id": usuario_id}) as rec:
        mensajes = _historial(db, usuario_id, sesion) + [HumanMessage(pregunta)]
        fuentes: list[str] = []
        herramientas = _herramientas(db, repo, fuentes)
        filtro = f"El usuario está consultando el repositorio «{repo}»." if repo else ""
        t0 = time.monotonic()

        def ejecutar(modelo):
            fuentes.clear()
            agente = create_agent(modelo, herramientas, system_prompt=SISTEMA.format(filtro=filtro))
            return agente.invoke({"messages": mensajes}, config={"recursion_limit": 8})

        estado, proveedor, fallos = llm.con_fallback(ejecutar)
        nuevos = estado["messages"][len(mensajes):]
        usadas = [m.name for m in nuevos if isinstance(m, ToolMessage)]
        respuesta = llm.texto_de(estado["messages"][-1]) or "No tengo una respuesta para eso."

        db.add(Conversacion(usuario_id=usuario_id, sesion=sesion, rol="user", contenido=pregunta))
        db.add(Conversacion(usuario_id=usuario_id, sesion=sesion, rol="assistant",
                            contenido=respuesta, proveedor=proveedor))
        db.commit()

        auditar("ia:chatbot", usuario_id=usuario_id, entidad="conversacion", entidad_id=sesion, detalle={
            "proveedor": proveedor, "modelo": llm.nombre_modelo(proveedor), "fallos_previos": fallos,
            "herramientas": usadas, "entrada": pregunta[:300], "salida": respuesta[:300],
            "ms": int((time.monotonic() - t0) * 1000)})
        rec.resultado = {"proveedor": proveedor, "herramientas": usadas, "fuentes": len(fuentes),
                         "plan_b": bool(fallos)}
        return {"respuesta": respuesta, "fuentes": [{"archivo": a} for a in fuentes],
                "proveedor": proveedor, "sesion": sesion}


def historial(db: Session, usuario_id: int, sesion: str) -> list[dict]:
    filas = (db.query(Conversacion)
             .filter(Conversacion.usuario_id == usuario_id, Conversacion.sesion == sesion)
             .order_by(Conversacion.id).all())
    return [{"rol": f.rol, "contenido": f.contenido, "proveedor": f.proveedor,
             "fecha": f.fecha.isoformat() if f.fecha else None} for f in filas]


def borrar_historial(db: Session, usuario_id: int, sesion: str) -> int:
    n = (db.query(Conversacion)
         .filter(Conversacion.usuario_id == usuario_id, Conversacion.sesion == sesion).delete())
    db.commit()
    auditar("borrar_conversacion", usuario_id=usuario_id, entidad="conversacion", entidad_id=sesion,
            detalle={"mensajes": n})
    return n

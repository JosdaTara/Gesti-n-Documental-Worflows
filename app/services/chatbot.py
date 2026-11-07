"""Chatbot del aplicativo: agente LangGraph con herramientas y memoria por usuario.

Reemplaza al agente RAG de n8n. El agente decide qué herramienta usar:
- `buscar_documentos`: búsqueda semántica en el repositorio (RAG).
- `listar_documentos`: estado del repositorio (cuántos, cuáles con error, por categoría).
- `consultar_facturas`: facturas registradas (las crea el módulo de visión).
- `consultar_alertas`: alertas abiertas de los flujos (proveedor nuevo, monto alto, duplicados).
- `resumen_compras`: total comprado por proveedor en un periodo.
Además, el usuario puede enviar la foto de una factura por el chat (`recibir_imagen`):
el módulo de visión la lee, dispara la automatización y el chatbot responde con el resultado.

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
from app.models import Alerta, Conversacion, Documento, Factura, Proveedor, Repositorio
from app.services import llm

SISTEMA = """Eres el asistente del sistema de gestión documental de la empresa.
Responde siempre en español, de forma breve y concreta.
- Para preguntas sobre el contenido de los documentos usa `buscar_documentos` y responde SOLO con lo que digan los fragmentos; cita el nombre del archivo.
- Para preguntas sobre cuántos documentos hay, su estado o errores usa `listar_documentos`.
- Para preguntas sobre facturas, proveedores, NIT o totales usa `consultar_facturas`.
- Para alertas o pendientes de revisión usa `consultar_alertas`; para cuánto se ha comprado usa `resumen_compras`.
- Si el usuario quiere registrar una factura, dile que puede enviar la foto con el botón 📎 del chat.
- Si la información no está, dilo claramente. No inventes datos.
{filtro}"""


def _herramientas(db: Session, repo_defecto: str, fuentes: list[str], usuario: dict | None = None):
    from app.core.security import tiene_permiso
    ve_todas = usuario is None or tiene_permiso(usuario, "ver_todas_facturas")

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
        if not ve_todas:
            q = q.filter(Factura.creado_por == usuario["id"])
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

    @tool
    def consultar_alertas(estado: str = "abierta") -> str:
        """Lista las alertas de los flujos de automatización. estado: abierta|atendida."""
        alertas = (db.query(Alerta).filter(Alerta.estado == (estado or "abierta"))
                   .order_by(Alerta.id.desc()).limit(15).all())
        if not alertas:
            return f"No hay alertas en estado {estado or 'abierta'}."
        return "\n".join(f"- [{a.severidad}] {a.tipo}: {a.mensaje}" for a in alertas)

    @tool
    def resumen_compras(desde: str = "", hasta: str = "") -> str:
        """Total comprado por proveedor (facturas validadas o confirmadas). Fechas opcionales AAAA-MM-DD."""
        from app.services.validacion_factura import normalizar_fecha
        q = db.query(Factura).filter(Factura.estado.in_(("validada", "confirmada")))
        if not ve_todas:
            q = q.filter(Factura.creado_por == usuario["id"])
        if normalizar_fecha(desde):
            q = q.filter(Factura.fecha >= normalizar_fecha(desde))
        if normalizar_fecha(hasta):
            q = q.filter(Factura.fecha <= normalizar_fecha(hasta))
        totales: dict[str, list] = {}
        for f in q.all():
            t = totales.setdefault(f.proveedor or "Sin proveedor", [0, 0.0])
            t[0] += 1; t[1] += f.total or 0
        if not totales:
            return "No hay compras registradas en ese periodo."
        filas = sorted(totales.items(), key=lambda kv: -kv[1][1])
        return "\n".join(f"- {p}: {n} factura(s), total {v:,.0f} COP".replace(",", ".") for p, (n, v) in filas) + \
            f"\nTotal general: {sum(v for _, (n, v) in filas):,.0f} COP".replace(",", ".")

    herramientas = [buscar_documentos, listar_documentos, consultar_facturas, resumen_compras]
    if usuario is None or tiene_permiso(usuario, "ver_alertas"):
        herramientas.append(consultar_alertas)
    return herramientas


def _historial(db: Session, usuario_id: int, sesion: str):
    filas = (db.query(Conversacion)
             .filter(Conversacion.usuario_id == usuario_id, Conversacion.sesion == sesion)
             .order_by(Conversacion.id.desc()).limit(settings.CHAT_HISTORY_TURNS).all())
    return [HumanMessage(f.contenido) if f.rol == "user" else AIMessage(f.contenido) for f in reversed(filas)]


def responder(db: Session, usuario_id: int, pregunta: str, sesion: str | None = None, repo: str = "",
              usuario: dict | None = None) -> dict:
    sesion = (sesion or "general")[:80]
    with log_execution(db, "chatbot", "web", {"pregunta": pregunta, "sesion": sesion, "repo": repo,
                                              "usuario_id": usuario_id}) as rec:
        mensajes = _historial(db, usuario_id, sesion) + [HumanMessage(pregunta)]
        fuentes: list[str] = []
        herramientas = _herramientas(db, repo, fuentes, usuario)
        filtro = f"El usuario está consultando el repositorio «{repo}»." if repo else ""
        t0 = time.monotonic()

        def ejecutar(modelo):
            fuentes.clear()
            agente = create_agent(modelo, herramientas, system_prompt=SISTEMA.format(filtro=filtro))
            return agente.invoke({"messages": mensajes}, config={"recursion_limit": 8})

        # una sola vez por proveedor: en una conversación es mejor pasar rápido al siguiente
        # que repetir el agente completo (puede tardar más de un minuto si el servicio está lento)
        estado, proveedor, fallos = llm.con_fallback(ejecutar, intentos=1)
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


def _cop(v) -> str:
    return f"{v:,.0f}".replace(",", ".") if isinstance(v, (int, float)) else "¿?"


def resumen_factura(f: dict) -> str:
    """Mensaje del chatbot con el resultado de leer una factura."""
    partes = [f"Leí la factura de {f.get('proveedor') or 'un proveedor que no pude identificar'}"
              + (f" (NIT {f['nit']})" if f.get("nit") else "")
              + (f" del {f['fecha']}" if f.get("fecha") else "")
              + (f" por {_cop(f.get('total'))} {f.get('moneda') or 'COP'}" if f.get("total") else "") + "."]
    partes.append(f"Confianza de la lectura: {round((f.get('confianza') or 0) * 100)} %.")
    if f.get("estado") == "validada":
        partes.append("Quedó registrada en compras y procesada automáticamente.")
    else:
        partes.append("Necesito que revises y confirmes los datos en la pestaña Facturas antes de registrarla.")
        partes += [f"• {a}" for a in (f.get("advertencias") or [])[:3]]
    alertas = [a for a in (f.get("alertas") or []) if a.get("estado") == "abierta"]
    if alertas:
        partes.append("Alertas generadas:")
        partes += [f"⚠ {a['mensaje']}" for a in alertas]
    return "\n".join(partes)


def recibir_imagen(db: Session, usuario: dict, data: bytes, mime: str, sesion: str | None = None,
                   consentimiento: bool = False) -> dict:
    """Canal chatbot del módulo de visión: el usuario envía la foto de una factura por el chat."""
    from app.services import vision_service
    sesion = (sesion or "general")[:80]
    f = vision_service.process_invoice(db, data, mime, usuario["id"], consentimiento, canal="chatbot")
    respuesta = resumen_factura(f)
    db.add(Conversacion(usuario_id=usuario["id"], sesion=sesion, rol="user", contenido="[Envió la foto de una factura]"))
    db.add(Conversacion(usuario_id=usuario["id"], sesion=sesion, rol="assistant", contenido=respuesta,
                        proveedor=(f.get("metodo") or "vision")[:20]))
    db.commit()
    return {"respuesta": respuesta, "fuentes": [], "proveedor": f.get("metodo"), "sesion": sesion, "factura": f}

"""Capa única de modelos de lenguaje con plan B.

Orden por defecto: Gemini (nivel gratuito) → Groq (nivel gratuito) → Ollama (local).
Solo se usan los proveedores configurados en las variables de entorno. Si todos fallan
(cuota agotada, red caída, clave inválida) se lanza `LLMNoDisponible`, que la API
convierte en un 503 con un mensaje claro para el usuario.

Cada llamada exitosa queda en la tabla `auditoria` con el proveedor que respondió.
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, TypeVar

from app.core.audit import auditar
from app.core.config import settings
from app.core.logging import con_reintentos

log = logging.getLogger("motor.llm")
T = TypeVar("T")


class LLMNoDisponible(RuntimeError):
    """Ningún proveedor de IA respondió. No tiene sentido reintentar de inmediato."""
    reintentable = False

    def __init__(self, mensaje: str, fallos: list[str] | None = None):
        super().__init__(mensaje)
        self.fallos = fallos or []


@dataclass
class RespuestaLLM:
    texto: str
    proveedor: str
    fallos: list[str] = field(default_factory=list)


# --- proveedores ---------------------------------------------------------------

def proveedores_configurados() -> list[str]:
    disponibles = {
        "gemini": bool(settings.GEMINI_API_KEY),
        "groq": bool(settings.GROQ_API_KEY),
        "ollama": bool(settings.OLLAMA_BASE_URL),
    }
    orden = [p.strip().lower() for p in settings.LLM_PROVIDERS.split(",") if p.strip()]
    return [p for p in orden if disponibles.get(p)]


def crear_modelo(proveedor: str, temperatura: float = 0.2):
    """Devuelve un chat model de LangChain para el proveedor indicado."""
    if proveedor == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=settings.GEMINI_MODEL, google_api_key=settings.GEMINI_API_KEY,
                                      temperature=temperatura, max_retries=1, timeout=60)
    if proveedor == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(model=settings.GROQ_MODEL, api_key=settings.GROQ_API_KEY,
                        temperature=temperatura, max_retries=1, timeout=60)
    if proveedor == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=settings.OLLAMA_MODEL, base_url=settings.OLLAMA_BASE_URL,
                          temperature=temperatura)
    raise ValueError(f"Proveedor desconocido: {proveedor}")


def nombre_modelo(proveedor: str) -> str:
    return {"gemini": settings.GEMINI_MODEL, "groq": settings.GROQ_MODEL,
            "ollama": settings.OLLAMA_MODEL}.get(proveedor, "?")


def con_fallback(tarea: Callable[[object], T], *, temperatura: float = 0.2) -> tuple[T, str, list[str]]:
    """Ejecuta `tarea(modelo)` con cada proveedor en orden hasta que uno funcione.

    Devuelve (resultado, proveedor_usado, fallos_previos).
    """
    proveedores = proveedores_configurados()
    if not proveedores:
        raise LLMNoDisponible("No hay ningún modelo de IA configurado (GEMINI_API_KEY, GROQ_API_KEY u OLLAMA_BASE_URL).")
    fallos: list[str] = []
    for p in proveedores:
        try:
            modelo = crear_modelo(p, temperatura)
            resultado = con_reintentos(intentos=2)(tarea)(modelo)
            if fallos:
                log.warning("Plan B: respondió %s tras fallar %s", p, fallos)
            return resultado, p, fallos
        except Exception as e:  # cuota, red, clave inválida, modelo caído...
            fallos.append(f"{p}: {type(e).__name__}: {str(e)[:200]}")
            log.warning("Proveedor %s falló: %s", p, e)
    raise LLMNoDisponible(
        "El asistente de IA no está disponible en este momento (se agotó la cuota gratuita o el "
        "servicio no responde). Intenta de nuevo en unos minutos.", fallos)


# --- utilidades -----------------------------------------------------------------

def texto_de(mensaje) -> str:
    """Normaliza el contenido de un mensaje (Gemini puede devolver una lista de partes)."""
    c = getattr(mensaje, "content", mensaje)
    if isinstance(c, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c).strip()
    return str(c or "").strip()


def extraer_json(texto: str) -> dict:
    """Extrae el primer objeto JSON de una respuesta (tolera ```json ... ```)."""
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip())
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", texto, re.S)
        if not m:
            raise ValueError("La respuesta de la IA no contiene JSON")
        return json.loads(m.group(0))


# --- API pública ----------------------------------------------------------------

def invocar(prompt: str, *, accion: str, usuario_id: int | None = None,
            temperatura: float = 0.2, sistema: str | None = None) -> RespuestaLLM:
    """Envía un prompt al primer proveedor disponible y audita la llamada."""
    from langchain_core.messages import HumanMessage, SystemMessage
    mensajes = ([SystemMessage(sistema)] if sistema else []) + [HumanMessage(prompt)]
    t0 = time.monotonic()
    respuesta, proveedor, fallos = con_fallback(lambda m: m.invoke(mensajes), temperatura=temperatura)
    texto = texto_de(respuesta)
    auditar(f"ia:{accion}", usuario_id=usuario_id, entidad="llm", detalle={
        "proveedor": proveedor, "modelo": nombre_modelo(proveedor), "fallos_previos": fallos,
        "entrada": prompt[:300], "salida": texto[:300], "ms": int((time.monotonic() - t0) * 1000)})
    return RespuestaLLM(texto, proveedor, fallos)


def invocar_json(prompt: str, *, accion: str, usuario_id: int | None = None) -> tuple[dict, str]:
    r = invocar(prompt + "\n\nResponde SOLO con un objeto JSON válido, sin texto adicional.",
                accion=accion, usuario_id=usuario_id, temperatura=0.1)
    return extraer_json(r.texto), r.proveedor


@con_reintentos
def embed(textos: list[str], tarea: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    """Embeddings con Gemini (google-genai).

    No hay fallback a otro proveedor porque los vectores de modelos distintos no son
    comparables: si Gemini no responde, el documento queda en `error` y se reintenta luego.
    """
    if not settings.GEMINI_API_KEY:
        raise LLMNoDisponible("No hay GEMINI_API_KEY configurada para generar embeddings.")
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    cfg = types.EmbedContentConfig(task_type=tarea, output_dimensionality=settings.EMBEDDING_DIM)
    vectores: list[list[float]] = []
    for i in range(0, len(textos), 100):  # límite de la API por lote
        r = client.models.embed_content(model=settings.EMBEDDING_MODEL, contents=textos[i:i + 100], config=cfg)
        vectores.extend(list(e.values) for e in r.embeddings)
    return vectores

"""Motor de automatización: registro de ejecuciones y reintentos.

Cada flujo migrado desde n8n se ejecuta dentro de `log_execution`, que deja una fila
en la tabla `ejecuciones` con fecha, disparador, entrada, resultado, error y duración.
Las llamadas a servicios externos (Gemini, Storage) se envuelven con `con_reintentos`,
que reintenta con espera exponencial y anota cuántos reintentos hubo en la ejecución.
"""
import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.models import Ejecucion

log = logging.getLogger("motor")

# Reintentos ocurridos dentro de la ejecución en curso (uno por flujo activo).
_reintentos: ContextVar[list | None] = ContextVar("reintentos", default=None)

MAX_JSON = 4000


def _json_seguro(valor):
    """Convierte a algo serializable en JSON y recorta textos largos."""
    if valor is None:
        return None
    texto = json.dumps(valor, default=str, ensure_ascii=False)
    if len(texto) > MAX_JSON:
        return {"recortado": True, "vista_previa": texto[:MAX_JSON]}
    return json.loads(texto)


@contextmanager
def log_execution(db: Session, flujo: str, disparador: str = "manual", entrada=None):
    """Registra una ejecución de un flujo. El llamador puede asignar `rec.resultado`."""
    rec = Ejecucion(flujo=flujo, disparador=disparador, entrada=_json_seguro(entrada))
    db.add(rec)
    db.commit()
    db.refresh(rec)
    t0 = time.monotonic()
    reintentos: list = []
    token = _reintentos.set(reintentos)
    try:
        yield rec
    except Exception as e:
        # Se descartan cambios a medio hacer del flujo antes de guardar el registro.
        db.rollback()
        if isinstance(e, HTTPException):
            rec.resultado = {"ok": False, "status": e.status_code}
            rec.error = str(e.detail)
        else:
            rec.resultado = {"ok": False}
            rec.error = f"{type(e).__name__}: {e}"
        log.warning("flujo=%s id=%s error=%s", flujo, rec.id, rec.error)
        raise
    else:
        res = rec.resultado if isinstance(rec.resultado, dict) else {}
        rec.resultado = {"ok": True, **res}
    finally:
        _reintentos.reset(token)
        if reintentos:
            rec.resultado = {**(rec.resultado or {}), "reintentos": len(reintentos)}
        rec.resultado = _json_seguro(rec.resultado)
        rec.fin = datetime.now(timezone.utc)
        rec.duracion_ms = int((time.monotonic() - t0) * 1000)
        db.commit()


def _es_reintentable(e: BaseException) -> bool:
    # Errores de validación o de datos no se arreglan reintentando.
    return not isinstance(e, (HTTPException, ValueError, KeyError, TypeError))


def _anotar_reintento(estado) -> None:
    lista = _reintentos.get()
    if lista is not None:
        lista.append(str(estado.outcome.exception()))
    log.warning("reintento %s de %s: %s", estado.attempt_number,
                getattr(estado.fn, "__name__", "?"), estado.outcome.exception())


def con_reintentos(fn=None, *, intentos: int | None = None):
    """Decorador: reintenta llamadas a servicios externos con espera exponencial."""
    def decorar(f):
        return retry(
            stop=stop_after_attempt(intentos or settings.RETRY_ATTEMPTS),
            wait=wait_exponential(multiplier=settings.RETRY_WAIT_SECONDS, max=30),
            retry=retry_if_exception(_es_reintentable),
            before_sleep=_anotar_reintento,
            reraise=True,
        )(f)
    return decorar(fn) if fn else decorar

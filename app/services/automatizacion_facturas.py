"""Flujo `procesar_factura`: lo que ocurre en compras cuando el módulo de visión entrega una factura.

Se dispara automáticamente cuando una factura queda `validada` (alta confianza de la IA) o
cuando un usuario la `confirma` tras revisarla. Pasos:
  1. Registrar o actualizar el proveedor (por NIT) y sus acumulados de compras.
  2. Aplicar reglas de control y abrir alertas para revisión humana:
       - proveedor_nuevo     primera factura de ese NIT
       - nit_invalido        el dígito de verificación no cuadra
       - sin_nit             la factura no trae NIT
       - posible_duplicado   ya existe otra factura con el mismo NIT, fecha y total
       - monto_alto          total ≥ MONTO_APROBACION: requiere aprobación
       - monto_atipico       total > MONTO_ATIPICO_FACTOR × promedio del proveedor
  3. Marcar la factura como procesada (idempotente: no se procesa dos veces).
Si el flujo falla, la factura queda sin `procesada_en` y el barrido periódico la reintenta.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import log_execution
from app.models import Alerta, Factura, Proveedor
from app.services.validacion_factura import normalizar_nit

ESTADOS_PROCESABLES = ("validada", "confirmada")


def _alerta(tipo: str, severidad: str, mensaje: str, f: Factura) -> Alerta:
    return Alerta(tipo=tipo, severidad=severidad, mensaje=mensaje, entidad="factura", entidad_id=str(f.id))


def _cop(v: float | None) -> str:
    return f"${v:,.0f}".replace(",", ".") if v is not None else "?"


def procesar_factura(db: Session, factura_id: str, disparador: str = "vision") -> dict:
    with log_execution(db, "procesar_factura", disparador, {"factura_id": str(factura_id)}) as rec:
        f = db.query(Factura).filter(Factura.id == uuid.UUID(str(factura_id))).first()
        if not f:
            rec.resultado = {"omitida": "no existe"}
            return rec.resultado
        if f.procesada_en or f.estado not in ESTADOS_PROCESABLES:
            rec.resultado = {"omitida": "ya procesada" if f.procesada_en else f"estado {f.estado}"}
            return rec.resultado

        alertas: list[Alerta] = []
        nombre = f.proveedor or "proveedor sin nombre"
        proveedor = None
        proveedor_nuevo = False

        if f.nit:
            nit_norm, estado_nit = normalizar_nit(f.nit)
            if estado_nit in ("dv_invalido", "invalido"):
                alertas.append(_alerta("nit_invalido", "alta",
                                       f"El NIT {f.nit} de {nombre} no es válido (dígito de verificación). Verifique con el proveedor.", f))
            proveedor = db.query(Proveedor).filter(Proveedor.nit == f.nit).first()
            if not proveedor:
                proveedor = Proveedor(nit=f.nit, nombre=nombre[:255], facturas=0, total_compras=0.0)
                db.add(proveedor); db.flush()
                proveedor_nuevo = True
                alertas.append(_alerta("proveedor_nuevo", "baja",
                                       f"Nuevo proveedor registrado: {nombre} (NIT {f.nit}). Verifique que esté autorizado.", f))
        else:
            alertas.append(_alerta("sin_nit", "media", f"La factura de {nombre} no tiene NIT: no se puede asociar a un proveedor.", f))

        if f.nit and f.total is not None:
            dup = (db.query(Factura)
                   .filter(Factura.id != f.id, Factura.nit == f.nit, Factura.total == f.total,
                           Factura.fecha == f.fecha, Factura.estado.in_(ESTADOS_PROCESABLES))
                   .first())
            if dup:
                alertas.append(_alerta("posible_duplicado", "alta",
                                       f"Posible factura duplicada: ya existe otra de {nombre} por {_cop(f.total)} con la misma fecha.", f))

        if f.total is not None and f.total >= settings.MONTO_APROBACION:
            alertas.append(_alerta("monto_alto", "alta",
                                   f"Factura de {nombre} por {_cop(f.total)}: supera {_cop(settings.MONTO_APROBACION)} y requiere aprobación.", f))
        elif proveedor and f.total and proveedor.facturas >= 3:
            promedio = proveedor.total_compras / proveedor.facturas
            if f.total > settings.MONTO_ATIPICO_FACTOR * promedio:
                alertas.append(_alerta("monto_atipico", "media",
                                       f"El total {_cop(f.total)} es inusual para {nombre} (promedio {_cop(promedio)}).", f))

        if proveedor and f.total:
            proveedor.facturas += 1
            proveedor.total_compras += f.total
        db.add_all(alertas)
        f.procesada_en = datetime.now(timezone.utc)
        db.commit()
        rec.resultado = {"alertas": [a.tipo for a in alertas], "proveedor_nuevo": proveedor_nuevo}
        return rec.resultado


def disparar(db: Session, factura_id: str, disparador: str) -> dict | None:
    """Llamada desde el módulo de visión: si el flujo falla no se pierde la factura;
    el error queda en `ejecuciones` y el barrido la reintenta."""
    try:
        return procesar_factura(db, factura_id, disparador)
    except Exception:
        return None


def barrido_pendientes(db: Session, disparador: str = "cron") -> dict:
    """Cola de reintento: procesa las facturas aprobadas que aún no se procesaron."""
    with log_execution(db, "barrido_facturas", disparador) as rec:
        ids = [str(i) for (i,) in db.query(Factura.id)
               .filter(Factura.procesada_en.is_(None), Factura.estado.in_(ESTADOS_PROCESABLES)).all()]
        ok = sum(1 for i in ids if disparar(db, i, disparador) is not None)
        rec.resultado = {"pendientes": len(ids), "procesadas": ok}
        return rec.resultado

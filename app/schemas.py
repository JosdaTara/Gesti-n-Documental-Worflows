from pydantic import BaseModel, Field
from typing import Optional, List, Any, Dict
from datetime import datetime

class LoginIn(BaseModel):
    usuario: str
    clave: str

class LoginOut(BaseModel):
    token: str
    nombre: str
    rol: str

class RepoCreate(BaseModel):
    nombre: str

class DocOut(BaseModel):
    id: str
    nombre: str
    repo: str
    tipo: str
    estado: str
    categoria: Optional[str] = ""
    fecha: str
    resumen: Optional[str] = ""
    datos: Optional[Dict[str, Any]] = None
    detalle: Optional[str] = ""

class ListarOut(BaseModel):
    documentos: List[DocOut]
    repos: List[str]

class BuscarIn(BaseModel):
    q: str
    repo: Optional[str] = ""

class BuscarItem(BaseModel):
    archivo: str
    pagina: Optional[int] = None
    fragmento: str

class BuscarOut(BaseModel):
    resultados: List[BuscarItem]

class ConsultarIn(BaseModel):
    pregunta: str
    repo: Optional[str] = ""
    sesion: Optional[str] = None

class ConsultarOut(BaseModel):
    respuesta: str
    fuentes: List[Dict[str, Any]] = []

class VisionItem(BaseModel):
    descripcion: str
    cantidad: int = 1
    valor: float = 0.0

class VisionFactura(BaseModel):
    proveedor: Optional[str] = None
    fecha: Optional[str] = None
    nit: Optional[str] = None
    moneda: str = "COP"
    items: List[VisionItem] = []
    total: Optional[float] = None
    confianza: float = 0.0

class VisionOut(BaseModel):
    factura: VisionFactura
    estado: str  # por_confirmar | validada
    imagen_id: str

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
    proveedor: Optional[str] = None   # modelo de IA que respondió (gemini|groq|ollama)
    sesion: Optional[str] = None

class VisionItem(BaseModel):
    descripcion: str = ""
    cantidad: float = 1
    valor: float = 0.0

class FacturaOut(BaseModel):
    id: str
    imagen_id: Optional[str] = None
    proveedor: Optional[str] = None
    fecha: Optional[str] = None
    nit: Optional[str] = None
    moneda: str = "COP"
    items: List[VisionItem] = []
    total: Optional[float] = None
    confianza: float = 0.0
    confianza_campos: Dict[str, float] = {}
    estado: str                      # por_confirmar | validada | confirmada
    metodo: Optional[str] = None     # vision:gemini | ocr+groq | texto:gemini | manual
    origen: Optional[str] = None     # foto | pdf_digital | pdf_escaneado
    advertencias: List[str] = []
    creado_en: Optional[str] = None
    revisado_en: Optional[str] = None

class FacturaEdit(BaseModel):
    proveedor: str = Field(..., min_length=1, max_length=255)
    fecha: Optional[str] = None
    nit: Optional[str] = None
    moneda: Optional[str] = "COP"
    total: float = Field(..., gt=0)
    items: Optional[List[VisionItem]] = None

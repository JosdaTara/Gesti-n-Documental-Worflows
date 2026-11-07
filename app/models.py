import uuid
from sqlalchemy import Boolean, Column, Integer, String, Text, Float, BigInteger, Date, DateTime, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.db import Base

class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True)
    usuario = Column(String(80), unique=True, nullable=False)
    clave_hash = Column(String(255), nullable=False)
    rol = Column(String(20), nullable=False, default="operador")   # admin | operador | consulta
    activo = Column(Boolean, nullable=False, default=True)
    creado_en = Column(DateTime(timezone=True), server_default=func.now())

class Sesion(Base):
    __tablename__ = "sesiones"
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    token_jti = Column(String(120), unique=True, nullable=False)
    expira = Column(DateTime(timezone=True), nullable=False)
    creado_en = Column(DateTime(timezone=True), server_default=func.now())

class Repositorio(Base):
    __tablename__ = "repositorios"
    id = Column(Integer, primary_key=True)
    nombre = Column(String(120), unique=True, nullable=False)
    creado_en = Column(DateTime(timezone=True), server_default=func.now())

class Documento(Base):
    __tablename__ = "documentos"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    storage_key = Column(String(500), nullable=False)
    nombre = Column(String(255), nullable=False)
    repo_id = Column(Integer, ForeignKey("repositorios.id"))
    tipo = Column(String(20))
    estado = Column(String(20), nullable=False, default="pendiente")
    categoria = Column(String(60))
    resumen = Column(Text)
    datos = Column(JSON)
    detalle = Column(Text)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"))
    fecha = Column(DateTime(timezone=True), server_default=func.now())
    hash_sha256 = Column(String(64))
    tamano = Column(BigInteger)

class Chunk(Base):
    __tablename__ = "chunks"
    id = Column(Integer, primary_key=True)
    documento_id = Column(UUID(as_uuid=True), ForeignKey("documentos.id", ondelete="CASCADE"), nullable=False)
    pagina = Column(Integer)
    contenido = Column(Text, nullable=False)
    embedding = Column(JSON)   # vector(768) en prod; JSON para portabilidad en tests
    meta = Column(JSON)

class Factura(Base):
    __tablename__ = "facturas"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    documento_id = Column(UUID(as_uuid=True), ForeignKey("documentos.id"))
    imagen_id = Column(UUID(as_uuid=True), ForeignKey("imagenes.id"))
    proveedor = Column(String(255))
    fecha = Column(Date)
    nit = Column(String(40))
    moneda = Column(String(8), default="COP")
    items = Column(JSON)
    total = Column(Float)
    confianza = Column(Float)
    confianza_campos = Column(JSON)       # confianza por campo (0-1)
    metodo = Column(String(60))           # vision:gemini | ocr+groq | manual ...
    origen = Column(String(20))           # foto | pdf_digital | pdf_escaneado
    advertencias = Column(JSON)
    datos_ia = Column(JSON)               # lo que extrajo la IA, para medir aciertos tras la revisión
    estado = Column(String(20), nullable=False, default="por_confirmar")  # por_confirmar|validada|confirmada
    revisado_por = Column(Integer, ForeignKey("usuarios.id"))
    revisado_en = Column(DateTime(timezone=True))
    procesada_en = Column(DateTime(timezone=True))   # cuándo la tomó el flujo procesar_factura
    creado_por = Column(Integer, ForeignKey("usuarios.id"))
    creado_en = Column(DateTime(timezone=True), server_default=func.now())

class Imagen(Base):
    __tablename__ = "imagenes"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    storage_key = Column(String(500), nullable=False)
    mime = Column(String(80))
    tamano = Column(BigInteger)
    hash_sha256 = Column(String(64))
    subido_por = Column(Integer, ForeignKey("usuarios.id"))
    subido_en = Column(DateTime(timezone=True), server_default=func.now())
    acl = Column(String(40), nullable=False, default="privado")

class Ejecucion(Base):
    __tablename__ = "ejecuciones"
    id = Column(Integer, primary_key=True)
    flujo = Column(String(80), nullable=False)
    disparador = Column(String(40))
    entrada = Column(JSON)
    resultado = Column(JSON)
    error = Column(Text)
    inicio = Column(DateTime(timezone=True), server_default=func.now())
    fin = Column(DateTime(timezone=True))
    duracion_ms = Column(Integer)

class Auditoria(Base):
    __tablename__ = "auditoria"
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"))
    accion = Column(String(80), nullable=False)
    entidad = Column(String(40))
    entidad_id = Column(String(80))
    detalle = Column(JSON)
    ip = Column(String(60))
    fecha = Column(DateTime(timezone=True), server_default=func.now())

class Conversacion(Base):
    """Memoria del chatbot: mensajes por usuario y sesión de chat."""
    __tablename__ = "conversaciones"
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    sesion = Column(String(80), nullable=False, index=True)
    rol = Column(String(12), nullable=False)        # user | assistant
    contenido = Column(Text, nullable=False)
    proveedor = Column(String(20))                  # modelo que respondió (solo assistant)
    fecha = Column(DateTime(timezone=True), server_default=func.now())


class Proveedor(Base):
    """Proveedores conocidos (se crean automáticamente con la primera factura)."""
    __tablename__ = "proveedores"
    id = Column(Integer, primary_key=True)
    nit = Column(String(40), unique=True, nullable=False)
    nombre = Column(String(255), nullable=False)
    facturas = Column(Integer, nullable=False, default=0)
    total_compras = Column(Float, nullable=False, default=0.0)
    creado_en = Column(DateTime(timezone=True), server_default=func.now())


class Alerta(Base):
    """Alertas generadas por los flujos de automatización para que un humano las atienda."""
    __tablename__ = "alertas"
    id = Column(Integer, primary_key=True)
    tipo = Column(String(40), nullable=False)       # proveedor_nuevo | monto_alto | monto_atipico | posible_duplicado | nit_invalido
    severidad = Column(String(10), nullable=False, default="media")   # baja | media | alta
    mensaje = Column(Text, nullable=False)
    entidad = Column(String(40))
    entidad_id = Column(String(80))
    estado = Column(String(12), nullable=False, default="abierta")    # abierta | atendida
    creado_en = Column(DateTime(timezone=True), server_default=func.now())
    atendida_por = Column(Integer, ForeignKey("usuarios.id"))
    atendida_en = Column(DateTime(timezone=True))

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS usuarios (
    id SERIAL PRIMARY KEY,
    usuario VARCHAR(80) UNIQUE NOT NULL,
    clave_hash VARCHAR(255) NOT NULL,
    rol VARCHAR(20) NOT NULL DEFAULT 'usuario',
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sesiones (
    id SERIAL PRIMARY KEY,
    usuario_id INT NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    token_jti VARCHAR(120) UNIQUE NOT NULL,
    expira TIMESTAMPTZ NOT NULL,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS repositorios (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(120) UNIQUE NOT NULL,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documentos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    storage_key VARCHAR(500) NOT NULL,
    nombre VARCHAR(255) NOT NULL,
    repo_id INT REFERENCES repositorios(id),
    tipo VARCHAR(20),
    estado VARCHAR(20) NOT NULL DEFAULT 'pendiente',
    categoria VARCHAR(60),
    resumen TEXT,
    datos JSONB,
    detalle TEXT,
    usuario_id INT REFERENCES usuarios(id),
    fecha TIMESTAMPTZ NOT NULL DEFAULT now(),
    hash_sha256 VARCHAR(64),
    tamano BIGINT
);

CREATE TABLE IF NOT EXISTS chunks (
    id SERIAL PRIMARY KEY,
    documento_id UUID NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
    pagina INT,
    contenido TEXT NOT NULL,
    embedding vector(768),
    metadata JSONB
);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE TABLE IF NOT EXISTS facturas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    documento_id UUID REFERENCES documentos(id),
    proveedor VARCHAR(255),
    fecha DATE,
    nit VARCHAR(40),
    moneda VARCHAR(8) DEFAULT 'COP',
    items JSONB,
    total NUMERIC(18,2),
    confianza REAL,
    estado VARCHAR(20) NOT NULL DEFAULT 'por_confirmar',
    creado_por INT REFERENCES usuarios(id),
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS imagenes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    storage_key VARCHAR(500) NOT NULL,
    mime VARCHAR(80),
    tamano BIGINT,
    hash_sha256 VARCHAR(64),
    subido_por INT REFERENCES usuarios(id),
    subido_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    acl VARCHAR(40) NOT NULL DEFAULT 'privado'
);

CREATE TABLE IF NOT EXISTS ejecuciones (
    id SERIAL PRIMARY KEY,
    flujo VARCHAR(80) NOT NULL,
    disparador VARCHAR(40),
    entrada JSONB,
    resultado JSONB,
    error TEXT,
    inicio TIMESTAMPTZ NOT NULL DEFAULT now(),
    fin TIMESTAMPTZ,
    duracion_ms INT
);

CREATE TABLE IF NOT EXISTS auditoria (
    id SERIAL PRIMARY KEY,
    usuario_id INT REFERENCES usuarios(id),
    accion VARCHAR(80) NOT NULL,
    entidad VARCHAR(40),
    entidad_id VARCHAR(80),
    detalle JSONB,
    ip VARCHAR(60),
    fecha TIMESTAMPTZ NOT NULL DEFAULT now()
);

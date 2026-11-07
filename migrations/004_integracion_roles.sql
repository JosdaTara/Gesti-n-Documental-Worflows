-- Fase 4: integracion vision -> automatizacion, roles y alertas
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS activo BOOLEAN NOT NULL DEFAULT TRUE;
UPDATE usuarios SET rol = 'operador' WHERE rol = 'usuario';
ALTER TABLE usuarios ALTER COLUMN rol SET DEFAULT 'operador';

ALTER TABLE facturas ADD COLUMN IF NOT EXISTS procesada_en TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS proveedores (
  id            SERIAL PRIMARY KEY,
  nit           VARCHAR(40) UNIQUE NOT NULL,
  nombre        VARCHAR(255) NOT NULL,
  facturas      INT NOT NULL DEFAULT 0,
  total_compras DOUBLE PRECISION NOT NULL DEFAULT 0,
  creado_en     TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alertas (
  id           SERIAL PRIMARY KEY,
  tipo         VARCHAR(40) NOT NULL,
  severidad    VARCHAR(10) NOT NULL DEFAULT 'media',
  mensaje      TEXT NOT NULL,
  entidad      VARCHAR(40),
  entidad_id   VARCHAR(80),
  estado       VARCHAR(12) NOT NULL DEFAULT 'abierta',
  creado_en    TIMESTAMPTZ DEFAULT now(),
  atendida_por INT REFERENCES usuarios(id),
  atendida_en  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_alertas_estado ON alertas (estado, creado_en DESC);

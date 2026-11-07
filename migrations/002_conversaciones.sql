-- Fase 2: memoria del chatbot
CREATE TABLE IF NOT EXISTS conversaciones (
  id          SERIAL PRIMARY KEY,
  usuario_id  INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
  sesion      VARCHAR(80) NOT NULL,
  rol         VARCHAR(12) NOT NULL CHECK (rol IN ('user','assistant')),
  contenido   TEXT NOT NULL,
  proveedor   VARCHAR(20),
  fecha       TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_conversaciones_usuario_sesion ON conversaciones (usuario_id, sesion, id);

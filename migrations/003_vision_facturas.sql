-- Fase 3: modulo de vision (facturas)
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS imagen_id UUID REFERENCES imagenes(id);
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS confianza_campos JSONB;
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS metodo VARCHAR(60);
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS origen VARCHAR(20);
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS advertencias JSONB;
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS datos_ia JSONB;
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS revisado_por INT REFERENCES usuarios(id);
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS revisado_en TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS ix_facturas_estado ON facturas (estado, creado_en DESC);

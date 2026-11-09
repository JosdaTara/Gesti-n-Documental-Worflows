-- Fase 6: reemplaza el índice vectorial ivfflat por HNSW.
-- El ivfflat de 001 se creó sobre la tabla vacía, así que sus 100 "listas" quedaron sin entrenar y
-- con ivfflat.probes = 1 (valor por defecto) la búsqueda omitía resultados: en la prueba con
-- PostgreSQL + pgvector, "¿cuántos días de vacaciones tengo?" no devolvía nada aunque el documento
-- existía. HNSW no necesita entrenamiento y funciona bien desde el primer documento.
DROP INDEX IF EXISTS chunks_embedding_idx;
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx ON chunks
    USING hnsw (embedding vector_cosine_ops);

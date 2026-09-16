-- Optional only: run after the structured retrieval baseline has become a
-- measured bottleneck and the cluster revision supports pgvector.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS case_memories (
    memory_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    case_type TEXT NOT NULL,
    root_cause TEXT,
    resolution TEXT,
    metadata JSONB NOT NULL,
    embedding VECTOR(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_case_memories_structured
    ON case_memories(case_type, root_cause, created_at DESC);

-- Build only after representative embeddings have been loaded and recall has
-- been evaluated. HNSW is intentionally not created by the core migration.
-- CREATE INDEX idx_case_memories_embedding_hnsw
--   ON case_memories USING hnsw (embedding vector_cosine_ops);

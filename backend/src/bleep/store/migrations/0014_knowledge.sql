-- Knowledge search (#39): documents from the Docker docs snapshot, linked repos' docs and past
-- incident reports, each cut into chunks with a vector. `content_hash` covers what the chunks are
-- made from, so an unchanged document is never embedded again; `model` names the embedder, and a
-- document from another model is embedded again on the next refresh and left out of search until
-- then.
CREATE TABLE knowledge_documents (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('docker', 'repo', 'incident')),
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);
CREATE INDEX knowledge_documents_source ON knowledge_documents (source, model);

-- When a refresh that costs more than embedding last ran ("repos": GitHub requests) and what it
-- covered (the linked repos), so a restart with the same repos does not repeat it within the day.
CREATE TABLE knowledge_refreshes (
    name TEXT PRIMARY KEY,
    at TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT ''
);

CREATE TABLE knowledge_chunks (
    document_id TEXT NOT NULL REFERENCES knowledge_documents (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    heading TEXT NOT NULL,            -- "A > B", the headings above the chunk
    text TEXT NOT NULL,
    embedding BLOB NOT NULL,          -- float32, unit length
    PRIMARY KEY (document_id, position)
);

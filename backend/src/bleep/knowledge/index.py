"""The knowledge index in SQLite: documents cut into chunks, each with its vector, searched by
cosine with numpy (brute force, as memory does; a few thousand chunks take a few milliseconds).

`ingest` embeds a document only when its content hash or the embedder changed, so refreshing a
source on every boot costs nothing for pages already in. `sync` makes one source (or one prefix
of its ids, such as one repo) hold exactly the documents given. `search` returns the best chunk of
each of the `k` closest documents, so one long page cannot fill every slot.
See docs/design/knowledge.md.
"""

import asyncio
import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from bleep.knowledge.chunking import CHUNK_VERSION, MAX_TOKENS, OVERLAP, Chunk, chunk_markdown
from bleep.memory.embeddings import Embedder
from bleep.schemas.knowledge import KnowledgeDocument, KnowledgeHit, KnowledgeSource
from bleep.store.db import Database

HEADING_SEPARATOR = " > "
EMBED_BATCH = 32


@dataclass(frozen=True)
class SyncResult:
    embedded: int
    unchanged: int
    removed: int


def content_hash(doc: KnowledgeDocument, max_tokens: int, overlap: int) -> str:
    """What the chunks and their embedded text are made from, chunk settings and the chunking's
    version included."""
    payload = json.dumps([doc.title, doc.url, doc.text, max_tokens, overlap, CHUNK_VERSION])
    return hashlib.sha256(payload.encode()).hexdigest()


def embedded_text(title: str, heading: str, text: str) -> str:
    """A chunk as it is embedded: where it sits, then what it says. The title leads unless the
    headings already start with it."""
    where = (
        heading
        if heading.startswith(title)
        else HEADING_SEPARATOR.join(part for part in (title, heading) if part)
    )
    return f"{where}\n\n{text}"


class KnowledgeIndex:
    def __init__(
        self,
        db: Database,
        embedder: Embedder,
        *,
        max_tokens: int = MAX_TOKENS,
        overlap: int = OVERLAP,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.db = db
        self.embedder = embedder
        self.max_tokens = max_tokens
        self.overlap = overlap
        self.clock = clock

    async def _embed(self, texts: list[str]) -> np.ndarray:
        return await asyncio.to_thread(self.embedder.embed, texts)

    async def _stored(self, doc_id: str) -> tuple[str, str] | None:
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT content_hash, model FROM knowledge_documents WHERE id = ?", (doc_id,)
            ) as cursor:
                row = await cursor.fetchone()
        finally:
            await conn.close()
        return None if row is None else (row["content_hash"], row["model"])

    async def ingest(self, doc: KnowledgeDocument) -> bool:
        """Index `doc`, replacing an earlier version of it; False when it was already in, unchanged
        and from the current embedder, so nothing was embedded."""
        digest = content_hash(doc, self.max_tokens, self.overlap)
        if await self._stored(doc.id) == (digest, self.embedder.name):
            return False
        chunks = chunk_markdown(doc.text, self.max_tokens, self.overlap)
        vectors = await self._vectors(doc.title, chunks)
        conn = await self.db.connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            await conn.execute("DELETE FROM knowledge_documents WHERE id = ?", (doc.id,))
            await conn.execute(
                "INSERT INTO knowledge_documents"
                " (id, source, title, url, content_hash, model, ingested_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    doc.id,
                    doc.source,
                    doc.title,
                    doc.url,
                    digest,
                    self.embedder.name,
                    self.clock().isoformat(),
                ),
            )
            await conn.executemany(
                "INSERT INTO knowledge_chunks (document_id, position, heading, text, embedding)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        doc.id,
                        position,
                        HEADING_SEPARATOR.join(chunk.heading_path),
                        chunk.text,
                        np.asarray(vector, dtype=np.float32).tobytes(),
                    )
                    for position, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
                ],
            )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()
        return True

    async def _vectors(self, title: str, chunks: list[Chunk]) -> list[np.ndarray]:
        texts = [
            embedded_text(title, HEADING_SEPARATOR.join(c.heading_path), c.text) for c in chunks
        ]
        vectors: list[np.ndarray] = []
        for start in range(0, len(texts), EMBED_BATCH):
            vectors.extend(await self._embed(texts[start : start + EMBED_BATCH]))
        return vectors

    async def last_refresh(self, name: str) -> tuple[datetime, str] | None:
        """When the refresh `name` last ran and what it covered, if it ever ran."""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT at, scope FROM knowledge_refreshes WHERE name = ?", (name,)
            ) as cursor:
                row = await cursor.fetchone()
        finally:
            await conn.close()
        return None if row is None else (datetime.fromisoformat(row["at"]), row["scope"])

    async def mark_refresh(self, name: str, scope: str = "") -> None:
        """Note that the refresh `name` ran now, over `scope`."""
        conn = await self.db.connect()
        try:
            await conn.execute(
                "INSERT OR REPLACE INTO knowledge_refreshes (name, at, scope) VALUES (?, ?, ?)",
                (name, self.clock().isoformat(), scope),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def document_ids(self, prefix: str) -> set[str]:
        """The ids of the documents whose id starts with `prefix`."""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT id FROM knowledge_documents WHERE substr(id, 1, ?) = ?",
                (len(prefix), prefix),
            ) as cursor:
                return {row["id"] for row in await cursor.fetchall()}
        finally:
            await conn.close()

    async def remove(self, ids: Iterable[str]) -> int:
        """Forget the documents with these ids; how many there were."""
        ids = list(ids)
        if not ids:
            return 0
        conn = await self.db.connect()
        try:
            cursor = await conn.executemany(
                "DELETE FROM knowledge_documents WHERE id = ?", [(i,) for i in ids]
            )
            await conn.commit()
            return cursor.rowcount
        finally:
            await conn.close()

    async def sync(
        self,
        source: KnowledgeSource,
        docs: Iterable[KnowledgeDocument],
        prefix: str | None = None,
    ) -> SyncResult:
        """Make the documents under `prefix` (default: the whole source) exactly `docs`: new and
        changed ones are embedded, unchanged ones kept, the others removed."""
        docs = list(docs)
        prefix = prefix if prefix is not None else f"{source}:"
        strays = [d.id for d in docs if d.source != source or not d.id.startswith(prefix)]
        if strays:
            raise ValueError(f"not of source {source!r} under {prefix!r}: {', '.join(strays)}")
        embedded = 0
        for doc in docs:
            embedded += await self.ingest(doc)
        gone = await self.document_ids(prefix) - {d.id for d in docs}
        removed = await self.remove(gone)
        return SyncResult(embedded=embedded, unchanged=len(docs) - embedded, removed=removed)

    async def search(
        self, query: str, k: int = 5, sources: set[KnowledgeSource] | None = None
    ) -> list[KnowledgeHit]:
        """The best chunk of each of the `k` documents closest to `query`, closest first, from
        `sources` (every source when None)."""
        if k <= 0 or sources is not None and not sources:
            return []
        wanted = sorted(sources) if sources is not None else []
        where = f" AND d.source IN ({', '.join('?' for _ in wanted)})" if wanted else ""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT c.document_id, c.heading, c.text, c.embedding, d.source, d.title, d.url"
                " FROM knowledge_chunks c JOIN knowledge_documents d ON d.id = c.document_id"
                f" WHERE d.model = ?{where}",
                (self.embedder.name, *wanted),
            ) as cursor:
                rows = list(await cursor.fetchall())
        finally:
            await conn.close()
        if not rows:
            return []
        query_vector = (await self._embed([query]))[0]
        matrix = np.stack([np.frombuffer(row["embedding"], dtype=np.float32) for row in rows])
        scores = matrix @ query_vector
        best: dict[str, tuple[float, int]] = {}
        for index, score in enumerate(scores):
            doc_id = rows[index]["document_id"]
            if doc_id not in best or score > best[doc_id][0]:
                best[doc_id] = (float(score), index)
        ranked = sorted(best.values(), key=lambda pair: -pair[0])[:k]
        return [
            KnowledgeHit(
                source=rows[index]["source"],
                title=rows[index]["title"],
                url=rows[index]["url"],
                heading=rows[index]["heading"],
                text=rows[index]["text"],
                score=round(score, 4),
            )
            for score, index in ranked
        ]

import pytest

from bleep.knowledge.index import KnowledgeIndex
from bleep.memory.embeddings import FakeEmbedder
from bleep.schemas.knowledge import KnowledgeDocument
from bleep.store.db import Database

RESTART = KnowledgeDocument(
    id="docker:restart-policies",
    source="docker",
    title="Start containers automatically",
    url="https://docs.docker.com/engine/containers/start-containers-automatically/",
    text=(
        "# Start containers automatically\n\n## Use a restart policy\n\n"
        "The on-failure policy restarts a container that exits with a non-zero exit code.\n\n"
        "## Restart policy details\n\nA restart policy only takes effect after a container "
        "starts successfully and runs for at least ten seconds.\n"
    ),
)
VOLUMES = KnowledgeDocument(
    id="docker:volumes",
    source="docker",
    title="Volumes",
    url="https://docs.docker.com/engine/storage/volumes/",
    text="# Volumes\n\nA named volume keeps database data when the container is removed.\n",
)
SHOP_README = KnowledgeDocument(
    id="repo:acme/shop:README.md",
    source="repo",
    title="acme/shop README.md",
    url="https://github.com/acme/shop/blob/main/README.md",
    text="# Shop\n\nThe shop needs the database container shop-db and restarts on failure.\n",
)
PAST = KnowledgeDocument(
    id="incident:inc_1",
    source="incident",
    title="shop-web exits: database connection refused",
    url="/incidents/inc_1",
    text="Cause: shop-db was stopped. Action: docker.start shop-db. Outcome: fixed.",
)


class Env:
    def __init__(self, db: Database, embedder: FakeEmbedder) -> None:
        self.db, self.embedder = db, embedder
        self.index = KnowledgeIndex(db, embedder)

    def embedded(self) -> int:
        return sum(len(batch) for batch in self.embedder.calls)


@pytest.fixture
async def env(tmp_path) -> Env:
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    return Env(db, FakeEmbedder())


async def test_search_finds_the_section_that_answers(env):
    await env.index.ingest(RESTART)
    await env.index.ingest(VOLUMES)

    [hit, *_] = await env.index.search("container exits with a non-zero exit code on-failure")

    assert hit.source == "docker"
    assert hit.title == "Start containers automatically"
    assert hit.url == RESTART.url
    assert hit.heading == "Start containers automatically > Use a restart policy"
    assert "non-zero exit code" in hit.text
    assert 0 < hit.score <= 1


async def test_re_ingesting_an_unchanged_document_embeds_nothing(env):
    assert await env.index.ingest(RESTART) is True
    before = env.embedded()

    assert await env.index.ingest(RESTART) is False
    assert await env.index.ingest(RESTART.model_copy()) is False

    assert env.embedded() == before
    assert len(await env.index.search("restart policy", k=10)) == 1


async def test_a_changed_document_replaces_its_old_chunks(env):
    await env.index.ingest(VOLUMES)
    changed = VOLUMES.model_copy(update={"text": "# Volumes\n\ntmpfs mounts live in memory.\n"})

    assert await env.index.ingest(changed) is True

    [hit] = await env.index.search("volume", k=5)
    assert hit.text == "tmpfs mounts live in memory."


async def test_a_new_title_or_url_alone_re_ingests(env):
    await env.index.ingest(VOLUMES)

    assert await env.index.ingest(VOLUMES.model_copy(update={"title": "Docker volumes"})) is True
    [hit] = await env.index.search("volume")
    assert hit.title == "Docker volumes"


async def test_the_source_filter_keeps_other_sources_out(env):
    for doc in (RESTART, SHOP_README, PAST):
        await env.index.ingest(doc)

    query = "database container restarts on failure"
    repo = await env.index.search(query, sources={"repo"})
    mixed = await env.index.search(query, sources={"docker", "incident"})
    everything = await env.index.search(query)

    assert {h.source for h in repo} == {"repo"}
    assert {h.source for h in mixed} == {"docker", "incident"}
    assert {h.source for h in everything} == {"docker", "repo", "incident"}
    assert await env.index.search(query, sources=set()) == []


async def test_one_hit_per_document_and_at_most_k(env):
    long = KnowledgeDocument(
        id="docker:long",
        source="docker",
        title="Long",
        url="u",
        text="\n\n".join(f"## Part {n}\n\nrestart policy part {n}" for n in range(8)),
    )
    for doc in (long, RESTART, VOLUMES):
        await env.index.ingest(doc)

    hits = await env.index.search("restart policy", k=2)

    assert len(hits) == 2
    assert len({h.url for h in hits}) == 2
    assert hits[0].score >= hits[1].score


async def test_sync_removes_documents_gone_from_the_source_and_keeps_the_rest(env):
    await env.index.ingest(PAST)
    await env.index.sync("docker", [RESTART, VOLUMES])

    result = await env.index.sync("docker", [VOLUMES])

    assert result.removed == 1 and result.embedded == 0 and result.unchanged == 1
    hits = await env.index.search("restart policy volume database", k=10)
    assert {h.url for h in hits} == {VOLUMES.url, PAST.url}


async def test_sync_with_a_prefix_only_touches_that_prefix(env):
    other = SHOP_README.model_copy(
        update={"id": "repo:acme/blog:README.md", "url": "https://github.com/acme/blog"}
    )
    await env.index.ingest(SHOP_README)
    await env.index.ingest(other)

    await env.index.sync("repo", [], prefix="repo:acme/shop:")

    assert await env.index.document_ids("repo:") == {"repo:acme/blog:README.md"}


async def test_a_document_of_another_source_than_the_sync_is_refused(env):
    with pytest.raises(ValueError, match="source"):
        await env.index.sync("docker", [PAST])


async def test_documents_of_another_embedder_are_left_out_until_re_ingested(tmp_path):
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    await KnowledgeIndex(db, FakeEmbedder(name="old")).ingest(RESTART)
    index = KnowledgeIndex(db, FakeEmbedder(name="new"))

    assert await index.search("restart policy") == []
    assert await index.ingest(RESTART) is True
    assert len(await index.search("restart policy")) == 1


async def test_an_empty_index_or_an_empty_document_finds_nothing(env):
    assert await env.index.search("anything") == []

    empty = VOLUMES.model_copy(update={"text": "# Only a heading\n"})
    await env.index.ingest(empty)

    assert await env.index.search("volume") == []
    assert await env.index.document_ids("docker:") == {"docker:volumes"}


async def test_the_heading_path_is_part_of_what_is_embedded(env):
    await env.index.ingest(RESTART)

    embedded = [text for batch in env.embedder.calls for text in batch]

    assert any(
        text.startswith("Start containers automatically > Restart policy details")
        for text in embedded
    )


async def test_a_new_chunking_version_embeds_unchanged_documents_again(env, monkeypatch):
    await env.index.ingest(VOLUMES)

    monkeypatch.setattr("bleep.knowledge.index.CHUNK_VERSION", 999)

    assert await env.index.ingest(VOLUMES) is True


async def test_a_repo_doc_is_redacted_before_it_is_stored_or_embedded(env):
    token = "ghp_" + "a1B2c3D4" * 5
    readme = SHOP_README.model_copy(
        update={"text": f"# Shop\n\nDeploy with GITHUB_TOKEN={token} and restart shop-web.\n"}
    )

    await env.index.ingest(readme)

    conn = await env.db.connect()
    try:
        async with conn.execute("SELECT text FROM knowledge_chunks") as cursor:
            stored = " ".join(row["text"] for row in await cursor.fetchall())
    finally:
        await conn.close()
    sent = " ".join(text for batch in env.embedder.calls for text in batch)
    assert token not in stored and token not in sent
    assert "<redacted>" in stored and "restart shop-web" in stored
    [hit] = await env.index.search("deploy the shop", sources={"repo"})
    assert token not in hit.text
    assert not await env.index.ingest(readme)  # the same README again embeds nothing


async def test_the_docker_snapshot_keeps_its_examples_as_written(env):
    example = VOLUMES.model_copy(update={"text": "# Volumes\n\nPOSTGRES_PASSWORD: example\n"})

    await env.index.ingest(example)

    [hit] = await env.index.search("postgres password", sources={"docker"})
    assert "POSTGRES_PASSWORD: example" in hit.text
